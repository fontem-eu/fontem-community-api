"""Build tool schemas from the API's own OpenAPI spec.

Hand-written schemas drift the moment a route signature changes, and the
failure is invisible until call time: a renamed parameter produces a tool the
model calls correctly and the API rejects. So the schema is derived from the
spec the API actually serves. Marking an endpoint stays a human decision —
``x-agent-tool`` carries the name, the "when", and the group — but the
parameters, types and descriptions come from the route.

Scoping, not volume, is the safety property here. The registry may hold
dozens; ``select`` returns only the groups relevant to a turn, because our
eval shows models mis-selecting among *four* tools and one reaching for a
forbidden one. A large registry with a small per-turn surface is a different
thing from a large per-turn surface, and only the second is dangerous.
"""
from __future__ import annotations

import json

import httpx

# Same named set as catalogue.FETCH_ERRORS, kept local so this module has no
# dependency on the catalogue beyond a shared idea.
SPEC_ERRORS = (
    httpx.HTTPError,
    httpx.InvalidURL,
    json.JSONDecodeError,
    ValueError,
    TypeError,
    KeyError,
)

AGENT_TOOL_KEY = "x-agent-tool"
SPEC_TIMEOUT = 5.0
# Beyond this the schema block starts costing more context than the answers
# it enables. A hard cap is better than a slow drift into 20k-token turns.
MAX_TOOLS_PER_TURN = 12

_JSON_TYPES = {"integer": "integer", "number": "number",
               "boolean": "boolean", "string": "string", "array": "array"}


def _param_schema(param: dict) -> dict:
    schema = param.get("schema") or {}
    # anyOf shows up for `int | None`; take the first non-null branch.
    if "anyOf" in schema:
        schema = next((s for s in schema["anyOf"]
                       if s.get("type") != "null"), {})
    out = {"type": _JSON_TYPES.get(schema.get("type"), "string")}
    if out["type"] == "array":
        out["items"] = {"type": "string"}
    desc = param.get("description") or schema.get("description")
    if desc:
        out["description"] = desc
    return out


def _operation_tool(path: str, method: str, op: dict, base_path: str) -> dict | None:
    """One opted-in operation as a function schema, or None if it opted out."""
    mark = op.get(AGENT_TOOL_KEY)
    if not mark:
        return None
    props, required = _properties(op, set(mark.get("params") or ()))
    return {
        "type": "function",
        "function": {
            "name": mark["name"],
            # "when" rather than "what": models select tools by matching
            # intent, not by reading return shapes.
            "description": f"Use when {mark['when']}.",
            "parameters": {"type": "object", "properties": props,
                           "required": required},
        },
        # Carried alongside, not inside the schema the model sees.
        "_route": {"method": method.upper(),
                   "path": base_path.rstrip("/") + path,
                   "group": mark.get("group", "general"),
                   "core": bool(mark.get("core", False))},
    }


def _properties(op: dict, wanted: set[str]) -> tuple[dict, list[str]]:
    """Declared query parameters, filtered to the ones a tool should expose."""
    props: dict = {}
    required: list[str] = []
    for param in op.get("parameters") or []:
        name = param.get("name")
        if not name or (wanted and name not in wanted):
            continue
        props[name] = _param_schema(param)
        if param.get("required"):
            required.append(name)
    return props, required


def tools_from_spec(spec: dict, base_path: str = "") -> list[dict]:
    """Every endpoint that opted in, as an OpenAI-format function schema."""
    tools = [
        tool
        for path, methods in (spec.get("paths") or {}).items()
        for method, op in (methods or {}).items()
        if isinstance(op, dict)
        for tool in [_operation_tool(path, method, op, base_path)]
        if tool is not None
    ]
    return sorted(tools, key=lambda t: t["function"]["name"])


def select(tools: list[dict], groups: set[str] | None = None,
           limit: int = MAX_TOOLS_PER_TURN) -> list[dict]:
    """The surface for one turn: core always, then relevant groups, capped.

    Core tools are never scoped away, and they are never the ones dropped by
    the cap. They are the discovery path — you cannot ask for a statistic
    without first finding its dataset code — so scoping them behind a group
    guess would strand the model exactly where it already fails: telling the
    user data is not here when it is.

    Truncation is reported by the caller rather than silent. A tool the model
    never saw looks exactly like a tool it chose not to use, and that
    ambiguity would make every eval result unreadable.
    """
    core = [t for t in tools if t["_route"].get("core")]
    rest = [t for t in tools if not t["_route"].get("core")]
    if groups:
        rest = ([t for t in rest if t["_route"]["group"] in groups]
                + [t for t in rest if t["_route"]["group"] not in groups])
    # Core first so the cap eats the scoped tail, never the discovery path.
    return (core + rest)[:max(limit, len(core))]


async def fetch_tools(client: httpx.AsyncClient, api_url: str) -> list[dict]:
    """Load the spec and derive tools. Empty on any failure, never raising."""
    try:
        resp = await client.get(f"{api_url.rstrip('/')}/openapi.json",
                                timeout=SPEC_TIMEOUT)
        resp.raise_for_status()
        return tools_from_spec(resp.json())
    except SPEC_ERRORS:
        # The assistant works with its built-in tools if the spec is
        # unreachable. Losing the generated ones degrades an answer; raising
        # would lose the turn.
        return []


def _resolve_path(path: str, args: dict) -> tuple[str | None, dict]:
    """Substitute {placeholders} from args; the remainder is the query.

    Returns (None, _) when a placeholder is left unfilled, so the caller
    reports it rather than issuing a request to a literal "{gmr_id}".
    """
    # Build the query from what is NOT consumed by the path, rather than
    # mutating while iterating — same result, and nothing to get wrong.
    consumed = {k for k in args if "{" + k + "}" in path}
    for key in consumed:
        path = path.replace("{" + key + "}", str(args[key]))
    params = {k: v for k, v in args.items() if k not in consumed}
    return (None if "{" in path else path), params


async def execute(client: httpx.AsyncClient, base_url: str,
                  tools: list[dict], call: tuple[str, dict]) -> str:
    """Call the endpoint behind a generated tool.

    Path params are substituted and the rest go on the query string, so one
    executor covers every annotated route without a per-tool branch — which
    is the point of deriving schemas from the spec rather than writing them.
    """
    name, args = call
    tool = next((t for t in tools if t["function"]["name"] == name), None)
    if tool is None:
        return json.dumps({"error": f"Unknown tool: {name}"})
    raw = tool["_route"]["path"]
    path, params = _resolve_path(raw, args)
    if path is None:
        return json.dumps({"error": f"missing path parameter for {raw}"})
    try:
        # No timeout argument: the caller's client is configured with one,
        # and threading a second through here only creates two places for
        # the value to disagree.
        resp = await client.get(f"{base_url.rstrip('/')}{path}", params=params)
    except SPEC_ERRORS as exc:
        return json.dumps({"error": str(exc)[:200]})
    if resp.status_code >= 400:
        return json.dumps({"error": f"API {resp.status_code}"})
    return resp.text
