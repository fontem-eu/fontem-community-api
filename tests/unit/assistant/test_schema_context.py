"""The schema block: tiered by context window, served, never fatal.

The threshold is the design: a 1M-context model gets the schema for free in
prefill, a 32k local model keeps the get_schema tool instead. Both read the
same server payload, so neither can drift from the other or from the graph.
"""
# pylint: disable=missing-function-docstring
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from src.assistant import schema_context
from src.assistant.schema_context import SchemaContext, render, wants_schema

PAYLOAD = {
    "node_labels": [
        {"label": "Company", "count": 2021, "keys": ["country", "gmr_id", "name"]},
    ],
    "relationships": [
        {"type": "AWARDED_TO", "from": "Contract", "to": "Company", "count": 188},
    ],
    "conventions": ["Country codes are ISO-3166 alpha-3: 'RUS'."],
}


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ── the tier decision ─────────────────────────────────────────

def test_local_models_stay_below_the_threshold():
    assert not wants_schema(32_768)


def test_hosted_and_frontier_models_clear_it():
    assert wants_schema(131_072)
    assert wants_schema(1_048_576)


def test_the_threshold_itself_is_inclusive():
    # 128k exactly is "can afford it" — the boundary belongs to the models
    # the feature exists for, not to the ones it protects.
    assert wants_schema(schema_context.SCHEMA_MIN_CONTEXT_TOKENS)


# ── rendering ─────────────────────────────────────────────────

def test_direction_is_spelled_with_an_arrow():
    # Direction is the thing that was guessed wrong. Ambiguity here would
    # reproduce the zero-row query this whole feature answers.
    block = render(PAYLOAD)
    assert "(Contract)-[:AWARDED_TO]->(Company)" in block


def test_labels_carry_their_keys_and_counts():
    block = render(PAYLOAD)
    assert "Company (2021)" in block
    assert "country" in block


def test_conventions_survive_verbatim():
    assert "'RUS'" in render(PAYLOAD)


def test_an_overgrown_schema_is_capped_not_unbounded():
    fat = {
        "node_labels": [{"label": f"L{i}", "count": 1, "keys": []}
                        for i in range(200)],
        "relationships": [{"type": f"R{i}", "from": "A", "to": "B", "count": 1}
                          for i in range(200)],
        "conventions": [],
    }
    block = render(fat)
    assert block.count("\n  L") == schema_context.MAX_LABELS
    assert block.count("]->(") == schema_context.MAX_RELATIONSHIPS


# ── fetching ──────────────────────────────────────────────────

def _provider(handler):
    ctx = SchemaContext("http://fontem-api")
    transport = httpx.MockTransport(handler)
    orig = httpx.AsyncClient

    class _Client(orig):  # pylint: disable=too-few-public-methods
        def __init__(self, **kw):
            kw["transport"] = transport
            super().__init__(**kw)

    return ctx, _Client


def test_a_healthy_fetch_is_cached(monkeypatch):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        assert request.url.path == "/schema/graph"
        return httpx.Response(200, text=json.dumps(PAYLOAD))

    ctx, client = _provider(handler)
    monkeypatch.setattr(httpx, "AsyncClient", client)
    first = _run(ctx.block())
    second = _run(ctx.block())
    assert "AWARDED_TO" in first and first == second
    assert calls["n"] == 1, "the second call must come from cache"


def test_a_dead_server_yields_an_empty_block_not_an_error(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    ctx, client = _provider(handler)
    monkeypatch.setattr(httpx, "AsyncClient", client)
    assert _run(ctx.block()) == ""


def test_a_dead_server_keeps_serving_the_stale_block(monkeypatch):
    state = {"up": True}

    def handler(request):
        if not state["up"]:
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(200, text=json.dumps(PAYLOAD))

    ctx, client = _provider(handler)
    monkeypatch.setattr(httpx, "AsyncClient", client)
    good = _run(ctx.block())
    ctx._at = 0.0  # pylint: disable=protected-access  # expire the TTL
    state["up"] = False
    assert _run(ctx.block()) == good, "stale schema beats no schema"


def test_a_malformed_payload_is_treated_as_a_failed_fetch(monkeypatch):
    def handler(_request):
        return httpx.Response(200, text='{"node_labels": [{"nope": 1}]}')

    ctx, client = _provider(handler)
    monkeypatch.setattr(httpx, "AsyncClient", client)
    assert _run(ctx.block()) == ""


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
