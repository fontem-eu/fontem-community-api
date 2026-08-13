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
