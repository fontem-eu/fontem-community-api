"""Reading entity payloads from fontem-api.

Split out of the tool runtime so that module stays under the line limit,
but these belong together anyway: every function here is about the
shapes fontem-api returns and the traps in them.
"""

import json


#: Where an entity's display name lives, per endpoint. /companies returns
#: `company_name`, /authorities returns `authority_name`, and search
#: results use plain `name`. Reading only one of them is how every summary
#: came out as "(unnamed)" — including for entities that plainly had one.
_NAME_FIELDS = ("name", "company_name", "authority_name", "person_name")


def entity_name(props: dict) -> str:
    """The entity's display name, or "" when the profile carries none."""
    for field in _NAME_FIELDS:
        value = props.get(field)
        if value:
            return str(value)
    return ""


def _build_summary(label: str, props: dict, contract_count: int) -> str:
    """Produce a 1-2 sentence prose précis that the model can quote."""
    name = entity_name(props) or "(unnamed)"
    country = props.get("country") or props.get("country_iso") or "unknown country"
    base = f"{name} is a {label} ({country})"
    if contract_count > 0:
        base += f" with {contract_count} EU procurement contract(s) in the graph"
    else:
        base += " with no EU procurement contracts in the graph"
    return base + "."


def _capture_names_from_dict(name_cache: dict[str, str], payload: dict) -> None:
    """The dict-shaped branch of _capture_names. Extracted to drop the
    cognitive-complexity score below Sonar's 15 threshold.
    """
    # `search_entities` shape: {"companies":[...], "authorities":[...], ...}
    for collection in ("companies", "authorities", "persons", "lobbyists"):
        for item in payload.get(collection) or []:
            _capture_names(name_cache, item)
    # `investigate_entity` shape: {"props": {...}}
    if "props" in payload:
        _capture_names(name_cache, payload["props"])
    # Single entity dict. Read the name through entity_name rather than
    # payload["name"]: search results use `name`, but /companies returns
    # `company_name` and /authorities `authority_name`, so an
    # investigate_entity result recorded nothing and its id kept rendering
    # as a UUID in the status line. Same per-endpoint key trap that made
    # every summary say "(unnamed)".
    name = entity_name(payload)
    if not name:
        return
    for id_field in ("gmr_id", "authority_id", "entity_id", "tr_id"):
        if id_field in payload:
            name_cache[str(payload[id_field])] = name


def _capture_names(name_cache: dict[str, str], payload: dict | list) -> None:
    """Walk a tool result and remember any (id, name) pairs we see."""
    if isinstance(payload, dict):
        _capture_names_from_dict(name_cache, payload)
    elif isinstance(payload, list):
        for item in payload:
            _capture_names(name_cache, item)


# ── Trimming what the model is handed ──────────────────────────
#
# investigate_entity's raw result for a well-connected company measured
# 34,561 characters against a 14,000-character per-turn tool budget, so it
# reached the model truncated mid-JSON — a broken blob it cannot parse and
# cannot ask about. Three quarters of it was the graph neighbourhood, and
# 3,677 characters were an exact duplicate of the contracts list.
#
# Found by ASSIST-23 once it ran on a deterministic model: the scripted
# agent said "investigate_entity reported no contract count" and attached
# the payload, because the JSON it was handed ended mid-object.

#: How many characters of the result the neighbourhood may spend.
#:
#: A COUNT cap does not work here: a node costs a 36-character UUID plus a
#: company name, so 31 neighbours came to 9,158 characters — under a 40-node
#: cap and still over the per-result ceiling. Budgeting by size holds
#: whatever the names look like.
#:
#: The ceiling that binds is MAX_TOOL_RESULT_CHARS (8,000 for ONE result),
#: not the 14,000 the whole turn may spend. Sized to leave room for the
#: contracts and props alongside it.
_GRAPH_CHAR_BUDGET = 4_500


def slim_contract(row):
    """A contract row without its empty fields.

    A TED row carries ~25 keys and most are null for any given contract.
    They cost the model context on every turn and say nothing.
    """
    if not isinstance(row, dict):
        return row
    return {k: v for k, v in row.items()
            if v is not None and v != "" and v != [] and v != {}}


def slim_graph(graph):
    """The shape of the neighbourhood, not every neighbour's property bag.

    Keeps who is adjacent and how, and drops the per-node properties: the
    model can investigate any id it finds interesting, which is cheaper than
    carrying every neighbour's full record into every turn.

    Then fills up to `_GRAPH_CHAR_BUDGET` and stops, reporting the real
    totals either way — so a partial neighbourhood is never mistaken for the
    whole one. That distinction is the same one the truncation marker makes
    elsewhere: an explicit gap beats a confident partial.
    """
    if not isinstance(graph, dict):
        return graph
    nodes = graph.get("nodes") if isinstance(graph.get("nodes"), list) else []
    edges = graph.get("edges") if isinstance(graph.get("edges"), list) else []

    def _trim(items, keys, budget):
        kept, spent = [], 0
        for item in items:
            if not isinstance(item, dict):
                continue
            small = {k: item.get(k) for k in keys if item.get(k) is not None}
            cost = len(json.dumps(small, default=str)) + 1
            if spent + cost > budget:
                break
            kept.append(small)
            spent += cost
        return kept, spent

    # Edges are cheaper and say more about structure, so they are served
    # first; nodes take what is left.
    kept_edges, spent = _trim(edges, ("source", "target", "type"),
                              _GRAPH_CHAR_BUDGET // 2)
    kept_nodes, _ = _trim(nodes, ("id", "label", "type"),
                          _GRAPH_CHAR_BUDGET - spent)
    out = {
        "center": graph.get("center"),
        "nodes": kept_nodes,
        "edges": kept_edges,
        "node_count": len(nodes),
        "edge_count": len(edges),
    }
    if len(kept_nodes) < len(nodes) or len(kept_edges) < len(edges):
        out["truncated"] = True
        out["note"] = ("neighbourhood sampled to fit the tool budget; "
                       "investigate a specific id for its detail")
    return out


def slim_props(props):
    """Entity properties without the contracts already sent separately.

    `recent_contracts` is byte-for-byte the same list as the result's own
    `contracts` key. Sending it twice cost 3,677 characters of a 14,000
    budget for no information at all.
    """
    if not isinstance(props, dict):
        return props
    return {k: v for k, v in props.items() if k != "recent_contracts"}
