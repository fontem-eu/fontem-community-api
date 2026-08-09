"""The catalogue block exists to stop one specific failure.

A user asked for demographic data. The platform holds eight population
datasets and three health datasets. The assistant said Fontem has "no
demographic data, only procurement" — correctly, from what it had been told.
These tests pin the properties that make that answer impossible.
"""
import httpx
import pytest

from src.assistant.catalogue import (
    CatalogueCache, fetch_catalogue, format_catalogue,
)

GRAPH = {"nodes": {"Company": 4600127, "Contract": 2485067,
                   "CPV": 0, "LobbyInterest": 0},
         "relationships": 24129322}
DATASETS = [
    {"code": "demo_r_births", "label": "Live births x NUTS-3",
     "theme": "population"},
    {"code": "demo_r_mlifexp", "label": "Life expectancy x NUTS-2",
     "theme": "health"},
    {"code": "nama_10r_2gdp", "label": "GDP x NUTS-2", "theme": "economy"},
]


def test_demographic_themes_are_visible():
    """The regression that started this: population must be nameable."""
    block = format_catalogue({"nodes": GRAPH["nodes"], "datasets": DATASETS})
    assert "population" in block
    assert "health" in block


def test_zero_count_labels_are_omitted():
    """Configured-but-unloaded labels must not be advertised.

    Listing CPV: 0 would cause the mirror-image failure — promising data we
    do not hold — which on a transparency platform is worse than silence.
    """
    block = format_catalogue({"nodes": GRAPH["nodes"], "datasets": []})
    assert "Company" in block
    assert "CPV" not in block
    assert "LobbyInterest" not in block


def test_block_stays_small():
    """It rides in every system prompt; bloat is a latency regression.

    The prompt was cut 1954 -> 358 tokens for latency. A catalogue that
    undoes that trade is not worth having.
    """
    many = [{"code": f"d{i}", "label": f"Dataset number {i}", "theme": f"t{i % 13}"}
            for i in range(42)]
    block = format_catalogue({"nodes": GRAPH["nodes"], "datasets": many})
    assert len(block) < 2600, f"catalogue block grew to {len(block)} chars"


def test_empty_registries_produce_no_block():
    """No half-empty section: callers skip injection entirely."""
    assert format_catalogue({"nodes": {}, "datasets": []}) == ""


@pytest.mark.asyncio
async def test_one_registry_failing_still_yields_a_block():
    """Losing the Atlas must not cost us the graph, and vice versa."""
    class HalfBroken:
        async def get(self, url, timeout=None):  # pylint: disable=unused-argument
            if "atlas" in url:
                raise httpx.ConnectError("atlas down")
            return _Resp(GRAPH)

    cat = await fetch_catalogue(HalfBroken(), "http://api")
    assert cat["nodes"], "graph should survive an Atlas outage"
    assert cat["datasets"] == []
    assert "Company" in format_catalogue(cat)


@pytest.mark.asyncio
async def test_cache_serves_second_call_without_refetching():
    calls = {"n": 0}

    class Counting:
        async def get(self, url, timeout=None):  # pylint: disable=unused-argument
            calls["n"] += 1
            return _Resp(GRAPH if "graph" in url else DATASETS)

    cache = CatalogueCache(ttl=1000.0)
    first = await cache.get(Counting(), "http://api")
    after = calls["n"]
    second = await cache.get(Counting(), "http://api")
    assert first == second
    assert calls["n"] == after, "cache refetched inside its TTL"


@pytest.mark.asyncio
async def test_total_outage_degrades_to_empty_not_error():
    """A slow dashboard endpoint must never fail the user's turn.

    Raises a real httpx transport error rather than a bare RuntimeError.
    The handlers now name what they catch, so a test that raises something
    outside that set would be asserting against a contract we do not offer —
    and would have hidden the fact that the set is the contract.
    """
    class Dead:
        async def get(self, url, timeout=None):  # pylint: disable=unused-argument
            raise httpx.ConnectTimeout("everything is down")

    assert await CatalogueCache().get(Dead(), "http://api") == ""


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload
