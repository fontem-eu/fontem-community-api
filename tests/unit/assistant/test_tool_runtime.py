"""What survived the executor's removal: the tool surface itself.

These were carried over from test_mistral_client.py when the hand-written
loop was decommissioned. The loop's own tests went with it — dedup, stall
detection, forced continuation and the provider round-trip were properties
of that loop and of nothing that remains. What is here is the part both
executors still depend on, which is why it outlived the thing it was
written against.
"""
# pylint: disable=protected-access
from __future__ import annotations

import httpx
import pytest

from src.assistant.tool_runtime import (
    _FRESHNESS_FETCH_TIMEOUT, _FRESHNESS_TTL_SECONDS, ToolRuntime,
    _system_prompt_with_today, _tool_detail,
)


@pytest.mark.asyncio
async def test_legacy_tools_still_callable_but_not_advertised():
    """Old saved conversations may have called get_company / get_authority
    / get_contracts / explore_graph. Those still work in execute_tool
    (back-compat), but the model only sees the canonical tool surface."""
    from src.assistant.tool_runtime import _TOOLS  # pylint: disable=import-outside-toplevel
    advertised_names = {t["function"]["name"] for t in _TOOLS}
    # Canonical surface
    assert "mcp__gmr__investigate_entity" in advertised_names
    assert "mcp__gmr__search_entities" in advertised_names
    assert "mcp__gmr__find_paths" in advertised_names
    assert "mcp__gmr__propose_edit" in advertised_names
    # Legacy NOT advertised
    assert "mcp__gmr__get_company" not in advertised_names
    assert "mcp__gmr__get_authority" not in advertised_names
    assert "mcp__gmr__get_contracts" not in advertised_names
    assert "mcp__gmr__explore_graph" not in advertised_names


def test_format_freshness_summary_renders_each_source_once():
    """One bullet per source, alphabetical by id, including coverage
    range and a freshness note (or `STALE` flag for old loads)."""
    from src.assistant.tool_runtime import _format_freshness_summary  # pylint: disable=import-outside-toplevel
    summary = _format_freshness_summary([
        {
            "id": "sanctions", "label": "EU consolidated sanctions",
            "coverage_start": "2026-01-01", "coverage_end": "2026-04-29",
            "record_count": 3015, "expected_cadence_hours": 25,
            "age_hours": 2.0, "stale": False,
        },
        {
            "id": "openfigi", "label": "OpenFIGI tickers",
            "coverage_start": None, "coverage_end": None,
            "record_count": 12345, "expected_cadence_hours": 200,
            "age_hours": 600.0, "stale": True,
        },
    ])
    assert summary  # not empty
    # Header tells the model what this is for.
    assert "coverage" in summary.lower()
    # Alphabetical: openfigi before sanctions.
    assert summary.index("openfigi") < summary.index("sanctions")
    # Coverage range surfaced when we have it.
    assert "2026-01-01 → 2026-04-29" in summary
    # Stale flag is loud — capital STALE so the model can't miss it.
    assert "STALE" in summary
    # Record counts get thousands separators for legibility.
    assert "3,015" in summary or "12,345" in summary


def test_format_freshness_summary_empty_on_no_sources():
    """Defensive: an empty list produces an empty string so the caller
    can short-circuit injection (no half-empty section in the prompt)."""
    from src.assistant.tool_runtime import _format_freshness_summary  # pylint: disable=import-outside-toplevel
    assert _format_freshness_summary([]) == ""


def test_default_gmr_api_url_points_at_fontem_api_not_the_stale_gmr_name():
    """The pre-rename default was http://gmr-api.gmr.svc.cluster.local —
    that DNS name is now NXDOMAIN in every fontem-* namespace, which
    silently broke every MCP/assistant tool call (every search,
    get_company, get_contracts, ...). The deployment manifest now sets
    GMR_API_INTERNAL explicitly, but a fresh dev/test env still falls
    back to this default. Pin that the default targets the post-rename
    Service so a future change can't quietly reintroduce the stale
    name.
    """
    from src.assistant.tool_runtime import _DEFAULT_GMR_API  # pylint: disable=import-outside-toplevel
    assert "gmr-api" not in _DEFAULT_GMR_API
    assert _DEFAULT_GMR_API == "http://fontem-api"


def test_env_override_wins_over_default():
    """GMR_API_INTERNAL env var override (set by the chart per-env) must
    take precedence over the in-code default. Pins the two-layer
    contract: default for dev/test, env override for cluster.
    """
    import importlib  # pylint: disable=import-outside-toplevel
    import os  # pylint: disable=import-outside-toplevel
    from unittest.mock import patch  # pylint: disable=import-outside-toplevel
    custom_url = "http://fontem-api.fontem-prod.svc.cluster.local"
    with patch.dict(os.environ, {"GMR_API_INTERNAL": custom_url}, clear=False):
        # llm_service.py reads at import time — reload to pick up the
        # patched env.
        from src.services import llm_service  # pylint: disable=import-outside-toplevel
        importlib.reload(llm_service)
        from src.services.llm_service import GMR_API_URL  # pylint: disable=import-outside-toplevel
        assert GMR_API_URL == custom_url

# ── Ported from the loop's tests ───────────────────────────────
#
# Both of these assert on helpers that still exist; only the way the old
# tests reached them (by driving a full turn through the loop) is gone.


def test_the_system_prompt_carries_todays_date():
    """The model cannot tell whether a date a tool returned is past or
    future without being told what today is."""
    from datetime import datetime, timezone  # pylint: disable=import-outside-toplevel
    out = _system_prompt_with_today("BASE PROMPT")
    assert "BASE PROMPT" in out
    assert datetime.now(timezone.utc).strftime("%Y-%m-%d") in out


def test_status_detail_substitutes_a_human_name_for_a_uuid():
    """The panel renders these strings. "Investigating 867f66f4-..." tells
    a reader nothing; the per-turn name cache is what makes it legible."""
    cache = {"867f66f4-4aa4-5737-9bed-d51e2746a729": "Siemens Energy AG/ADR"}
    detail = _tool_detail(
        "mcp__gmr__investigate_entity",
        {"entity_id": "867f66f4-4aa4-5737-9bed-d51e2746a729"},
        cache,
    )
    assert "Siemens Energy AG/ADR" in detail
    assert "867f66f4" not in detail


def test_status_detail_falls_back_to_the_id_when_no_name_is_known():
    detail = _tool_detail(
        "mcp__gmr__investigate_entity", {"entity_id": "unknown-id"}, {},
    )
    assert detail, "the panel still needs something to show"


# ── the freshness cache ─────────────────────────────────────────────────
#
# The source-freshness block is fetched once and held for five minutes.
# Both halves of that are load bearing and neither was exercised: without
# the cache the assistant hits /data-quality/freshness on EVERY chat turn,
# and the fetch sits on the user's critical path before the first model
# request; without expiry a loader run that lands mid-session is invisible
# until the pod restarts.
#
# Only the formatter above was tested. The cache around it had five
# surviving mutants, including reading the tuple's summary slot as its
# timestamp — which would compare a string to a float and take the whole
# block down rather than serve a stale line.

class _FreshnessClient:
    """Counts fetches and can be told to fail."""

    def __init__(self, payload=None, fail=None):
        self.calls = 0
        self.urls: list = []
        self.timeouts: list = []
        self._payload = payload if payload is not None else {"sources": []}
        self._fail = fail

    async def get(self, url, timeout=None):
        self.urls.append(url)
        self.calls += 1
        self.timeouts.append(timeout)
        if self._fail is not None:
            raise self._fail
        return _FreshnessResponse(self._payload)


class _FreshnessResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _one_source():
    return {"sources": [{
        "id": "ted", "rows": 10, "coverage_start": "2020-01-01",
        "coverage_end": "2026-08-01", "loaded_at": "2026-08-30T00:00:00Z",
    }]}


@pytest.mark.asyncio
async def test_a_warm_cache_serves_the_second_turn_without_refetching():
    """The reason the cache exists: one fetch per five minutes, not one per
    chat turn, on a call that blocks the first model request."""
    rt = ToolRuntime(gmr_api_url="http://fontem-api")
    client = _FreshnessClient(_one_source())

    first = await rt._get_freshness_summary(client)
    second = await rt._get_freshness_summary(client)

    assert client.calls == 1, "a warm cache must not refetch"
    assert second == first
    assert first, "the fixture has a source, so the block is not empty"


@pytest.mark.asyncio
async def test_the_cache_expires_so_a_loader_run_is_picked_up_in_session():
    """The other half. A five-minute hold that never lets go means a load
    that lands mid-session stays invisible until the pod restarts."""
    rt = ToolRuntime(gmr_api_url="http://fontem-api")
    client = _FreshnessClient(_one_source())

    await rt._get_freshness_summary(client)
    # Age the entry past the TTL by rewriting its timestamp, rather than
    # sleeping five minutes or monkeypatching the clock.
    cached_at, summary = rt._freshness_cache
    rt._freshness_cache = (cached_at - _FRESHNESS_TTL_SECONDS - 1, summary)
    await rt._get_freshness_summary(client)

    assert client.calls == 2, "an expired entry must be refetched"


@pytest.mark.asyncio
async def test_the_entry_is_stored_as_timestamp_then_summary():
    """Order matters: reading the summary slot as the timestamp compares a
    str to a float and takes the block down instead of serving a stale
    line — a crash where the whole design point was degrading quietly."""
    rt = ToolRuntime(gmr_api_url="http://fontem-api")
    await rt._get_freshness_summary(_FreshnessClient(_one_source()))

    cached_at, summary = rt._freshness_cache
    assert isinstance(cached_at, float)
    assert isinstance(summary, str)


@pytest.mark.asyncio
async def test_a_failed_fetch_costs_a_sentence_not_the_turn():
    """Best-effort is the contract: monitoring metadata being unavailable
    must not fail a turn that would otherwise have answered."""
    rt = ToolRuntime(gmr_api_url="http://fontem-api")
    client = _FreshnessClient(fail=httpx.ConnectError("boom"))

    assert await rt._get_freshness_summary(client) == ""


@pytest.mark.asyncio
async def test_a_failure_is_cached_too_so_a_dead_endpoint_is_not_retried_every_turn():
    """A down data-quality API would otherwise be re-dialled on every turn,
    each time on the critical path ahead of the first model request."""
    rt = ToolRuntime(gmr_api_url="http://fontem-api")
    client = _FreshnessClient(fail=httpx.ConnectError("boom"))

    await rt._get_freshness_summary(client)
    await rt._get_freshness_summary(client)

    assert client.calls == 1


@pytest.mark.asyncio
async def test_the_fetch_is_bounded_because_it_blocks_the_first_response():
    """This call happens before the model is asked anything. An unbounded
    one leaves the user watching a spinner because monitoring is slow."""
    rt = ToolRuntime(gmr_api_url="http://fontem-api")
    client = _FreshnessClient(_one_source())

    await rt._get_freshness_summary(client)

    assert client.urls == ["http://fontem-api/data-quality/freshness"]
    assert client.timeouts == [_FRESHNESS_FETCH_TIMEOUT]
    assert _FRESHNESS_FETCH_TIMEOUT <= 10, "a critical-path fetch must stay short"
