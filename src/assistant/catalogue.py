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

  * ``/data-quality/graph``   — live node-label counts for the knowledge
    graph, so the block states what is actually loaded rather than what is
    configured. (``/data-quality/pipeline`` carries the richer DataSource
    registry but joins it against the events store and times out after 45s
    in production; the catalogue needs the inventory, not the health.)
  * ``/atlas/datasets``       — the Atlas catalogue of statistical datasets.

A feed shows up here the moment it is registered there, with no edit anywhere.

Both fetches are best-effort and independent. Losing one degrades the block
rather than emptying it — a turn that knows about the graph but not the Atlas
is worth far more than a turn that knows nothing.
"""
from __future__ import annotations

import asyncio
import time

import httpx

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
    """Pull both registries concurrently. Either may come back empty."""
    base = gmr_api_url.rstrip("/")
    graph, datasets = await asyncio.gather(
        _get_json(client, f"{base}/data-quality/graph"),
        _get_json(client, f"{base}/atlas/datasets"),
        return_exceptions=True,
    )
    nodes = graph.get("nodes") if isinstance(graph, dict) else None
    return {
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
    datasets = catalogue.get("datasets") or []
    if not nodes and not datasets:
        return ""

    lines: list[str] = []
    if nodes:
        lines.append("Knowledge graph — searchable with the entity tools:")
        for label, count in sorted(nodes.items(), key=lambda kv: -kv[1]):
            lines.append(f"- {label}: {count:,}")

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
        except (httpx.HTTPError, ValueError, TypeError):
            # Best-effort, exactly like the coverage block: a turn without the
            # catalogue is worse than a turn with it, and far better than a
            # turn that fails because a dashboard endpoint was slow.
            block = ""
        self._cached = (now, block)
        return block
