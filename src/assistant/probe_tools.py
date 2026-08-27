"""A direct, guarded probe into the data stores.

For the cases Studio ceremony does not fit — a one-off count, a keys check,
"does this label exist". The entire design constraint is protection parity
with what already guards the public surface: execution goes through the
same fontem-api proxies as the Studio's Run button and the SPARQL endpoint,
which enforce a write/DDL keyword denylist, an 8KB query cap, a 1000-row
cap, a statement timeout and reader-role credentials. This module adds no
engine, no credentials and no second opinion — a probe is a thin,
authenticated pass-through or it is a hole.

Anything worth keeping belongs in a Studio project instead (the prompt says
so): a probe result vanishes with the turn, a saved query can be re-run,
plotted and reviewed.
"""
from __future__ import annotations

import json

import httpx

from src.services.studio_validation import QUERY_PATHS

PROBE_TOOL_NAME = "mcp__gmr__query_graph"

PROBE_TIMEOUT = 30.0

PROBE_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": PROBE_TOOL_NAME,
            "description": (
                "Runs one read-only query directly against a data store "
                "and returns the rows. For quick probes — a count, a keys "
                "check — not for analysis you want to keep: save those as "
                "Studio queries instead, where they can be re-run and "
                "plotted. Strictly read-only, row-capped and time-limited; "
                "write keywords are refused. Get the schema before writing "
                "Cypher — relationship direction is not guessable."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "lang": {
                        "type": "string",
                        "enum": sorted(QUERY_PATHS),
                        "description": "cypher (graph), sql (stats "
                                       "warehouse) or sparql (RDF store).",
                    },
                    "query": {"type": "string"},
                },
                "required": ["lang", "query"],
            },
        },
    },
]


async def execute(client: httpx.AsyncClient, api_url: str, args: dict) -> str:
    """One probe, through the guarded proxy. Always returns JSON text."""
    lang = str(args.get("lang") or "").strip().lower()
    path = QUERY_PATHS.get(lang)
    if path is None:
        return json.dumps({
            "error": f"unknown lang {lang!r}",
            "hint": f"one of: {', '.join(sorted(QUERY_PATHS))}",
        })
    query = str(args.get("query") or "")
    if not query.strip():
        return json.dumps({"error": "query is required"})
    try:
        resp = await client.post(
            api_url.rstrip("/") + path,
            json={"query": query},
            timeout=PROBE_TIMEOUT,
        )
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        return json.dumps({
            "error": f"the {lang} engine did not answer: "
                     f"{type(exc).__name__}"})
    if resp.status_code >= 400:
        # The refusal detail IS the product here: "write keyword 'MERGE'
        # is not allowed" tells the model exactly what to change, where a
        # bare status code teaches it nothing.
        return json.dumps({
            "error": f"the {lang} proxy refused the query "
                     f"(HTTP {resp.status_code})",
            "detail": resp.text[:600],
        })
    return resp.text
