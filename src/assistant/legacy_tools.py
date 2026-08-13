"""Tool names kept callable for conversations saved before the tool revamp.

Not advertised in `_TOOLS`, so no model will choose one — they exist only so
replaying an old thread does not error. Split out of the tool runtime because
they are pure request-building with no client state, and that module is
capped at 1000 lines.
"""
from __future__ import annotations

import httpx

# Recognised here rather than by the caller, so adding a legacy alias means
# touching one file.
LEGACY_TOOLS = frozenset({
    "mcp__gmr__get_company",
    "mcp__gmr__get_authority",
    "mcp__gmr__get_contracts",
    "mcp__gmr__explore_graph",
})


async def fetch(client: httpx.AsyncClient, gmr_api_url: str, name: str,
                args: dict) -> httpx.Response | None:
    """Issue the request behind a legacy tool, or None if it is not one."""
    if name == "mcp__gmr__get_company":
        return await client.get(
            f"{gmr_api_url}/companies/{args.get('gmr_id', '')}")
    if name == "mcp__gmr__get_authority":
        return await client.get(
            f"{gmr_api_url}/authorities/{args.get('authority_id', '')}")
    if name == "mcp__gmr__get_contracts":
        eid = args.get("entity_id", "")
        params = {"limit": args.get("limit", 20)}
        resp = await client.get(
            f"{gmr_api_url}/companies/{eid}/contracts", params=params)
        if resp.status_code == 404:
            # An id can be either kind; the old tool tried company first and
            # fell through to authority. Preserved so replays behave.
            resp = await client.get(
                f"{gmr_api_url}/authorities/{eid}/contracts", params=params)
        return resp
    if name == "mcp__gmr__explore_graph":
        return await client.get(
            f"{gmr_api_url}/graph/{args.get('entity_id', '')}",
            params={"depth": args.get("depth", 1)})
    return None
