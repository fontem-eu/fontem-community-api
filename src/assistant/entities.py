"""Reading entity payloads from fontem-api.

Split out of the tool runtime so that module stays under the line limit,
but these belong together anyway: every function here is about the
shapes fontem-api returns and the traps in them.
"""


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

#: Neighbourhood caps. Generous enough to describe who is adjacent, small
#: enough that the whole result fits the budget with room for the contracts.
_MAX_GRAPH_NODES = 40
_MAX_GRAPH_EDGES = 60


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
    model can investigate any id it finds interesting, which is cheaper
    than carrying every neighbour's full record into every turn. Says what
    it dropped, so the model knows the list is a sample rather than the
    whole neighbourhood.
    """
    if not isinstance(graph, dict):
        return graph
    nodes = graph.get("nodes") if isinstance(graph.get("nodes"), list) else []
    edges = graph.get("edges") if isinstance(graph.get("edges"), list) else []
    out = {
        "center": graph.get("center"),
        "nodes": [
            {k: n.get(k) for k in ("id", "label", "type") if n.get(k) is not None}
            for n in nodes[:_MAX_GRAPH_NODES] if isinstance(n, dict)
        ],
        "edges": [
            {k: e.get(k) for k in ("source", "target", "type") if e.get(k) is not None}
            for e in edges[:_MAX_GRAPH_EDGES] if isinstance(e, dict)
        ],
        "node_count": len(nodes),
        "edge_count": len(edges),
    }
    if len(nodes) > _MAX_GRAPH_NODES or len(edges) > _MAX_GRAPH_EDGES:
        out["truncated"] = True
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
