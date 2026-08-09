"""What data the platform actually holds, assembled at runtime.

The assistant used to be told, in prose, that Fontem covers "public
procurement, corporate ownership, lobbying and democratic accountability".
That sentence is true and badly incomplete: the platform also serves 40-odd
Eurostat regional datasets — demography, health, crime, migration, education.
Asked about population, the model answered that Fontem holds no demographic
data. It was reasoning correctly from what it had been told.

Hand-written scope prose cannot be kept honest. Every new feed would need
someone to remember to edit a paragraph in a different repository, and the
failure mode is silent: the model does not say "my list may be stale", it
says "we don't have that". So the block is built from the same registries the
platform uses to render its own dashboards:

  * ``/catalogue`` — every producer's own DataDescription plus the Atlas
    dataset catalogue, in one call. Each ETL loader declares what it
    publishes next to the code that publishes it, so the two cannot drift.

Falls back to ``/data-quality/graph`` + ``/atlas/datasets`` when ``/catalogue``
is not there yet, so this ships without waiting on the API deploy. The
fallback is strictly worse — node counts say what is loaded but not what it
covers or what it can answer — which is why it is a fallback and not the
design.

A feed shows up here the moment it is registered there, with no edit anywhere.

Both fetches are best-effort and independent. Losing one degrades the block
rather than emptying it — a turn that knows about the graph but not the Atlas
is worth far more than a turn that knows nothing.
"""
from __future__ import annotations

import asyncio
import json
import time

import httpx

# Everything a best-effort registry fetch can legitimately raise, named
# rather than swallowed behind `except Exception`. The list is short because
# the surface is small: a transport failure, a non-2xx, a body that is not
# the JSON we expect, or a payload whose shape has drifted.
#
# json.JSONDecodeError subclasses ValueError, and httpx.HTTPStatusError and
# TimeoutException both subclass HTTPError; they are named anyway so the next
# reader does not have to know the hierarchy to see what is handled.
FETCH_ERRORS = (
    httpx.HTTPError,
    httpx.InvalidURL,
    json.JSONDecodeError,
    ValueError,
    TypeError,
    KeyError,
)

# Long enough that the catalogue costs one fetch per pod per hour; short
# enough that a newly registered feed becomes visible the same day.
CATALOGUE_TTL_SECONDS = 3600.0
CATALOGUE_FETCH_TIMEOUT = 5.0

# Themes carry the meaning here, not individual dataset codes. Listing all 42
# Atlas datasets costs ~7k characters and buys little: the model does not need
# to recite `demo_r_births` from memory, it needs to know demography is here
# and that a tool will find the code. Examples are capped for the same reason.
_MAX_EXAMPLES_PER_THEME = 3


async def _get_json(client: httpx.AsyncClient, url: str):
    resp = await client.get(url, timeout=CATALOGUE_FETCH_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


async def fetch_catalogue(client: httpx.AsyncClient, gmr_api_url: str) -> dict:
    """Producer descriptions when available, node counts when not."""
    base = gmr_api_url.rstrip("/")
    try:
        payload = await _get_json(client, f"{base}/catalogue")
        # Presence of the key, not truthiness of the list. A platform that
        # legitimately has zero described producers has still answered the
        # question, and falling back there would fire two more requests to
        # relearn less.
        if isinstance(payload, dict) and "producers" in payload:
            return {
                "producers": payload.get("producers") or [],
                "datasets": payload.get("datasets") or [],
                "nodes": {},
            }
    except FETCH_ERRORS:
        # Any failure here means "try the older shape", never "fail the turn".
        pass
    return await _fetch_legacy(client, base)


async def _fetch_legacy(client: httpx.AsyncClient, base: str) -> dict:
    """Pre-/catalogue shape. Either half may come back empty."""
    graph, datasets = await asyncio.gather(
        _get_json(client, f"{base}/data-quality/graph"),
        _get_json(client, f"{base}/atlas/datasets"),
        return_exceptions=True,
    )
    nodes = graph.get("nodes") if isinstance(graph, dict) else None
    return {
        "producers": [],
        # Labels with a zero count are configured but unloaded. Listing them
        # would invite exactly the failure this block exists to prevent, in
        # the opposite direction: promising data we do not hold.
        "nodes": {k: v for k, v in (nodes or {}).items()
                  if isinstance(v, int) and v > 0},
        "datasets": datasets if isinstance(datasets, list) else [],
    }


def _group(rows: list[dict], theme_key: str, label_key: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        theme = str(row.get(theme_key) or "other")
        label = str(row.get(label_key) or row.get("id") or row.get("code") or "")
        if label:
            out.setdefault(theme, []).append(label)
    return out


def _graph_lines(producers: list, nodes: dict) -> list[str]:
    """Producer descriptions when we have them, node counts when we do not."""
    if producers:
        lines = ["Sources in the knowledge graph — searchable with the "
                 "entity tools:"]
        for prod in producers:
            label = prod.get("label") or prod.get("producer") or ""
            line = f"- {label}: {prod.get('summary') or ''}"
            # Coverage is the field that stops "0 results" being reported as
            # "absent from the world", so it is worth its characters.
            coverage = prod.get("coverage") or ""
            if coverage:
                line += f" ({coverage})"
            lines.append(line)
        return lines
    if nodes:
        return ["Knowledge graph — searchable with the entity tools:"] + [
            f"- {label}: {count:,}"
            for label, count in sorted(nodes.items(), key=lambda kv: -kv[1])]
    return []


def format_catalogue(catalogue: dict) -> str:
    """Compress both registries into a short block for the system prompt.

    Deliberately theme-level. The point is to stop the model asserting that
    data is absent when it is present; picking the right dataset code is the
    dataset-search tool's job, and duplicating the whole catalogue into every
    turn would cost more context than the answers are worth.
    """
    # Filtered here as well as at fetch time, deliberately. This is the
    # boundary that produces the text the model reads, so the invariant has
    # to hold no matter which caller assembled the dict.
    nodes = {k: v for k, v in (catalogue.get("nodes") or {}).items()
             if isinstance(v, int) and v > 0}
    producers = catalogue.get("producers") or []
    datasets = catalogue.get("datasets") or []
    if not nodes and not datasets and not producers:
        return ""

    lines: list[str] = _graph_lines(producers, nodes)

    if datasets:
        grouped = _group(datasets, "theme", "label")
        total = sum(len(v) for v in grouped.values())
        lines.append(
            f"\nAtlas — {total} Eurostat regional statistical datasets, "
            "by NUTS region and year. Use the dataset tools to find a code "
            "and read values:")
        for theme, labels in sorted(grouped.items()):
            shown = "; ".join(sorted(labels)[:_MAX_EXAMPLES_PER_THEME])
            more = len(labels) - _MAX_EXAMPLES_PER_THEME
            lines.append(f"- {theme} ({len(labels)}): {shown}"
                         + (f"; +{more} more" if more > 0 else ""))

    header = (
        "## What Fontem holds\n\n"
        "This list is generated from the platform's own source registries at "
        "the time of this turn, so it is complete and current. If a topic "
        "appears below, the data IS here — find it with a tool rather than "
        "telling the user it is absent.")
    return header + "\n\n" + "\n".join(lines)


class CatalogueCache:
    """One fetch per TTL per process, shared across turns.

    The client is an app-scoped singleton, so this cache is per-pod. Nothing
    user-specific is stored — the catalogue is the same for everyone.
    """

    def __init__(self, ttl: float = CATALOGUE_TTL_SECONDS) -> None:
        self._ttl = ttl
        self._cached: tuple[float, str] | None = None

    async def get(self, client: httpx.AsyncClient, gmr_api_url: str) -> str:
        now = time.monotonic()
        if self._cached is not None and now - self._cached[0] < self._ttl:
            return self._cached[1]
        try:
            block = format_catalogue(await fetch_catalogue(client, gmr_api_url))
        except FETCH_ERRORS:
            # This block is a nicety; the user's question is not. A slow
            # dashboard endpoint, a non-2xx or a drifted payload degrades to
            # no catalogue rather than to a failed turn.
            block = ""
        self._cached = (now, block)
        return block
