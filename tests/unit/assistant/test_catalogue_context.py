"""CatalogueContext: prefill first, live fetch second, "" as the floor."""
# pylint: disable=missing-function-docstring,protected-access
from __future__ import annotations

import asyncio

import httpx

from src.assistant import catalogue
from src.assistant.catalogue import CatalogueContext


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_the_mounted_prefill_wins_and_costs_no_fetch(monkeypatch):
    # Every deployed environment mounts llm-prefill, so this is the path
    # that actually runs in production.
    monkeypatch.setattr(catalogue, "read_prefill", lambda: "## What Dargle holds\n\nx")

    def _explode(*_a, **_kw):
        raise AssertionError("prefill was mounted; no fetch should happen")

    monkeypatch.setattr(httpx, "AsyncClient", _explode)
    assert _run(CatalogueContext("http://api").block()).startswith("## What Dargle holds")


def test_a_failing_fetch_costs_the_block_not_the_turn(monkeypatch):
    # A missing catalogue costs the model a few discovery calls. Failing the
    # turn over prompt garnish costs the user the turn.
    monkeypatch.setattr(catalogue, "read_prefill", lambda: "")

    class _Boom:
        async def __aenter__(self):
            raise httpx.ConnectError("no route")

        async def __aexit__(self, *_a):
            return False

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kw: _Boom())
    assert _run(CatalogueContext("http://api").block()) == ""
