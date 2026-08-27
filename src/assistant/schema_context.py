"""The graph schema as prompt material, for models that can afford it.

The schema lives on the server (`fontem-api /schema/graph`) so it can never
rot into a hardcoded string; this module fetches it, renders it compactly,
and decides which models get it in prefill.

The tier decision is the point. On a 1M-context model the rendered schema is
noise-level overhead and saves entire tool round-trips; on a 32k local model
it would eat a meaningful slice of the continuity window the panel rework
just widened. So the block is injected only above a context threshold, and
everyone below it keeps the `get_schema` tool — the same payload, paid for
with a turn instead of prefill.
"""
from __future__ import annotations

import time

import httpx

#: Context window, in tokens, above which the schema rides in the system
#: prompt. 128k splits the current roster exactly where it should: the local
#: models (32k) stay lean, the hosted set (131k) and ox-alpha (1M) get the
#: schema for free.
SCHEMA_MIN_CONTEXT_TOKENS = 128_000

#: The prompt is rebuilt every turn but the schema moves on ETL timescales.
#: Half an hour keeps a conversation's prefix stable enough for prefix
#: caching while still tracking a mid-day ontology deploy.
CACHE_TTL_SECONDS = 1800

FETCH_TIMEOUT = 5.0

#: Caps on the rendered block. The schema is worth ~2 tool round-trips, not
#: an unbounded share of the prefix; a graph that grows a hundred labels
#: should widen the endpoint's curation, not this prompt.
MAX_LABELS = 40
MAX_RELATIONSHIPS = 60


def wants_schema(context_tokens: int) -> bool:
    """Whether a model of this context size gets the schema in prefill."""
    return context_tokens >= SCHEMA_MIN_CONTEXT_TOKENS


def render(payload: dict) -> str:
    """The schema as compact prompt text.

    Direction is spelled with an arrow in every relationship line because
    direction is the thing that was guessed wrong: `(Company)-[]->(Contract)`
    returns zero rows on this graph.
    """
    lines: list[str] = ["Graph schema (live, from the platform):", "Nodes:"]
    for n in payload.get("node_labels", [])[:MAX_LABELS]:
        keys = ", ".join(n.get("keys", [])[:12])
        lines.append(f"  {n['label']} ({n.get('count', '?')}): {keys}")
    lines.append("Relationships (direction matters):")
    for r in payload.get("relationships", [])[:MAX_RELATIONSHIPS]:
        lines.append(
            f"  ({r['from']})-[:{r['type']}]->({r['to']})  x{r.get('count', '?')}")
    conventions = payload.get("conventions", [])
    if conventions:
        lines.append("Conventions:")
        lines.extend(f"  - {c}" for c in conventions)
    return "\n".join(lines)


class SchemaContext:
    """Fetches and caches the rendered schema block. Best-effort by design:
    a graph hiccup yields an empty block and the turn proceeds without a
    schema, exactly as every turn did before this existed."""

    def __init__(self, api_url: str, ttl: float = CACHE_TTL_SECONDS) -> None:
        self._url = api_url.rstrip("/") + "/schema/graph"
        self._ttl = ttl
        self._at = 0.0
        self._block = ""

    async def block(self) -> str:
        """The rendered schema, or "" when the server cannot answer."""
        now = time.time()
        if self._block and now - self._at < self._ttl:
            return self._block
        try:
            async with httpx.AsyncClient(timeout=FETCH_TIMEOUT) as client:
                resp = await client.get(self._url)
                resp.raise_for_status()
                rendered = render(resp.json())
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            # Keep whatever we had — a stale schema beats none, and none
            # beats failing the user's turn over prompt garnish.
            return self._block
        self._block = rendered
        self._at = now
        return self._block
