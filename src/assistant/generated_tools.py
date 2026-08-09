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


def tools_from_spec(spec: dict, base_path: str = "") -> list[dict]:
    """Every endpoint that opted in, as an OpenAI-format function schema."""
    tools: list[dict] = []
    for path, methods in (spec.get("paths") or {}).items():
        for method, op in (methods or {}).items():
            if not isinstance(op, dict):
                continue
            mark = op.get(AGENT_TOOL_KEY)
            if not mark:
                continue
            wanted = set(mark.get("params") or ())
            props, required = {}, []
            for param in op.get("parameters") or []:
                name = param.get("name")
                if not name or (wanted and name not in wanted):
                    continue
                props[name] = _param_schema(param)
                if param.get("required"):
                    required.append(name)
            tools.append({
                "type": "function",
                "function": {
                    "name": mark["name"],
                    # "when" rather than "what": models select tools by
                    # matching intent, not by reading return shapes.
                    "description": f"Use when {mark['when']}.",
                    "parameters": {"type": "object", "properties": props,
                                   "required": required},
                },
                # Carried alongside, not inside the schema the model sees.
                "_route": {"method": method.upper(),
                           "path": base_path.rstrip("/") + path,
                           "group": mark.get("group", "general")},
            })
    return sorted(tools, key=lambda t: t["function"]["name"])


def select(tools: list[dict], groups: set[str] | None = None,
           limit: int = MAX_TOOLS_PER_TURN) -> list[dict]:
    """The surface for one turn: relevant groups first, hard-capped.

    Truncation is reported by the caller rather than silent. A tool the model
    never saw looks exactly like a tool it chose not to use, and that
    ambiguity would make every eval result unreadable.
    """
    if groups:
        ranked = ([t for t in tools if t["_route"]["group"] in groups]
                  + [t for t in tools if t["_route"]["group"] not in groups])
    else:
        ranked = list(tools)
    return ranked[:limit]


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
