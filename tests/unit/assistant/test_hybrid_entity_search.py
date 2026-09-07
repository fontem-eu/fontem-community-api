"""Entity search goes through the hybrid store, not substring matching.

From the story-loop run of 2026-09-07T125415Z. Asked to write about EU
contracts won by Russian suppliers, the model searched for the buyers and
winners it had just been reading about:

    26. search_entities {"query": "Rosatom Service"}                 -> 0
    28. search_entities {"query": "AKB security Russia"}             -> 0
    30. search_entities {"query": "Kozloduy NPP"}                    -> 0
    31. search_entities {"query": "Bulgarian Industrial Centre ..."} -> 0
    32. search_entities {"query": "Institute of Plasma Physics ..."} -> 0

Five of eight empty, for entities that are all in the graph. `/search`
matched `toLower(name) CONTAINS toLower(q)`, and the register stores
`„АЕЦ Козлодуй“ ЕАД` and `Росатом Сервис АД` in Cyrillic — so the tool
found an entity only if you already spelled it the way the register did.
The model recovered the ids by dropping to raw Cypher (call 33).

`/search/results` — lexical + vector, RRF-fused, what the UI has used all
along — answers "Kozloduy NPP" with that same authority id.
"""
# pylint: disable=missing-function-docstring,protected-access
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from src.assistant.tool_runtime import ToolRuntime, _group_hybrid_results


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _Client:
    """Answers /search/results, /search, or raises — one per test."""

    def __init__(self, hybrid=None, legacy=None, hybrid_status=200):
        self.hybrid, self.legacy, self.hybrid_status = hybrid, legacy, hybrid_status
        self.paths: list[str] = []
        self.sent: list[dict] = []

    async def get(self, url, params=None):        # noqa: D102
        self.paths.append("results" if url.endswith("/search/results")
                          else url.rsplit("/", 1)[-1])
        self.sent.append(params or {})
        if url.endswith("/search/results"):
            return _Resp(self.hybrid, self.hybrid_status)
        return _Resp(self.legacy, 200)


class _Resp:
    def __init__(self, payload, status):
        self._payload, self.status_code = payload, status
        self.text = json.dumps(payload)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=None)

    def json(self):
        return self._payload


HYBRID = {"results": [
    {"type": "authority", "id": "dc8ad164", "title": "АЕЦ КОЗЛОДУЙ ЕАД",
     "country": "BGR"},
    {"type": "company", "id": "4b7d7b8a", "title": "KOOPE KFT.",
     "subtitle": "ROU"},
    {"type": "eu_lobbying", "id": "9053-20",
     "title": "Kozloduy NPP - New Build EAD", "country": "BULGARIA"},
    {"type": "contract", "id": "ignored", "title": "a contract, not an entity"},
]}


@pytest.fixture(name="runtime")
def _runtime():
    return ToolRuntime.__new__(ToolRuntime)


def test_a_latin_query_now_reaches_a_cyrillic_entity(runtime):
    runtime._gmr_api_url = "http://api"
    client = _Client(hybrid=HYBRID)
    out = json.loads(_run(runtime._search_entities(client, {"query": "Kozloduy NPP"})))
    assert out["authorities"][0]["authority_id"] == "dc8ad164"
    assert out["authorities"][0]["name"] == "АЕЦ КОЗЛОДУЙ ЕАД"
    assert client.paths == ["results"], "the hybrid store answered; no fallback"
    assert client.sent[0]["q"] == "Kozloduy NPP", "the query reaches the store"


def test_the_shape_is_what_the_tools_already_consume():
    # _capture_names walks companies/authorities/persons/lobbyists and reads
    # `name` plus a per-kind id field. A flat ranked list would go unseen and
    # every id would render as a UUID in the status line.
    grouped = _group_hybrid_results(HYBRID)
    assert set(grouped) == {"companies", "authorities", "persons", "lobbyists"}
    assert grouped["companies"][0]["gmr_id"] == "4b7d7b8a"
    assert grouped["lobbyists"][0]["tr_id"] == "9053-20"


def test_rows_that_are_not_entities_are_dropped():
    assert all(r["name"] != "a contract, not an entity"
               for rows in _group_hybrid_results(HYBRID).values() for r in rows)


def test_a_dead_hybrid_store_falls_back_rather_than_failing(runtime):
    # It is the weaker endpoint, but a degraded search beats a dead tool —
    # and the store is not up in every environment yet.
    runtime._gmr_api_url = "http://api"
    legacy = {"query": "x", "companies": [{"gmr_id": "g1", "name": "Legacy Co"}],
              "authorities": [], "persons": [], "lobbyists": []}
    client = _Client(hybrid=None, legacy=legacy, hybrid_status=500)
    out = json.loads(_run(runtime._search_entities(client, {"query": "x"})))
    assert out["companies"][0]["name"] == "Legacy Co"
    assert client.paths == ["results", "search"]


def test_a_malformed_hybrid_body_also_falls_back(runtime):
    runtime._gmr_api_url = "http://api"
    legacy = {"companies": [], "authorities": [], "persons": [], "lobbyists": []}
    client = _Client(hybrid="not-a-dict", legacy=legacy)
    out = json.loads(_run(runtime._search_entities(client, {"query": "x"})))
    assert out == legacy
