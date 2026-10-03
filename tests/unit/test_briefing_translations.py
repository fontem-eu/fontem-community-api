"""Briefing items in the reader's language (title_translations).

An item keeps the title it was stored with; the translation is looked up
when the item is shown, and the original stays beside it.
"""
# pylint: disable=missing-function-docstring,redefined-outer-name,unused-argument
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from src.domain.feed import FeedItem
from src.services.title_translations import (
    MAX_KEYS, HttpTitleTranslator, clean_lang, localise,
)
# The fixture, reused; pytest finds it by name.
from tests.unit.test_briefings_api import briefing  # pylint: disable=unused-import


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class FakeTranslator:
    def __init__(self, contracts=None, cohesion=None):
        self.found = {"contracts": contracts or {}, "cohesion": cohesion or {}}
        self.calls = []

    async def lookup(self, lang, contract_keys, cohesion_ids):
        self.calls.append((lang, list(contract_keys), list(cohesion_ids)))
        return self.found


def _contract(key="k1", title="Léčivý přípravek"):
    return FeedItem(item_id=key, title="Nemocnice awarded 1 EUR to X", summary=title,
                    facets={"kind": "contract", "headline": title})


def _grant(qid="Q1", title="Upgrading of the port"):
    return FeedItem(item_id=f"cohesion:{qid}", title=f"{title} — 5 EUR from ERDF",
                    summary="Transport ERDF", facets={"kind": "cohesion", "headline": title})


def test_clean_lang():
    assert [clean_lang(x) for x in ("de", "DE-at", "pt_BR", "xx", "", None)] == [
        "de", "de", "pt", None, None, None]


def test_a_contract_headline_and_summary_are_translated_and_the_original_kept():
    item = _contract()
    [out] = _run(localise([item], "en", FakeTranslator(contracts={"k1": "Medicinal product"})))
    assert out.facets["headline"] == "Medicinal product"
    assert out.facets["headline_original"] == "Léčivý přípravek"
    assert out.summary == "Medicinal product"
    # The Atom sentence is left alone, and the stored item is not changed.
    assert out.title == item.title
    assert item.facets["headline"] == "Léčivý přípravek"


def test_a_grant_headline_is_translated_but_its_programme_stays():
    [out] = _run(localise([_grant()], "de", FakeTranslator(cohesion={"Q1": "Ausbau des Hafens"})))
    assert out.facets["headline"] == "Ausbau des Hafens"
    assert out.facets["headline_original"] == "Upgrading of the port"
    assert out.summary == "Transport ERDF"


def test_untranslated_and_other_items_are_shown_as_stored():
    lobby = FeedItem(item_id="lobby:1", title="X updated its declaration",
                     facets={"kind": "lobbying", "headline": "X"})
    items = [_contract("k2"), lobby]
    fake = FakeTranslator()
    assert _run(localise(items, "en", fake)) == items
    # Only contracts and grants are looked up.
    assert fake.calls == [("en", ["k2"], [])]


def test_no_language_or_no_translator_means_no_lookup():
    fake = FakeTranslator(contracts={"k1": "x"})
    items = [_contract()]
    assert _run(localise(items, None, fake)) == items
    assert _run(localise(items, "en", None)) == items
    assert not fake.calls


# ── the HTTP client ───────────────────────────────────────────────────

def _transport(seen, body=None, status=200):
    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(status, json=body if body is not None else {
            "contracts": {"k1": {"title": "Medicinal product", "original": "Léčivý"}},
            "cohesion": {}})
    return httpx.MockTransport(handler)


def test_the_client_asks_fontem_api_once_and_remembers_hits_and_misses():
    seen = []
    client = HttpTitleTranslator(base_url="http://api", transport=_transport(seen))
    first = _run(client.lookup("en", ["k1", "k2", "k1"], []))
    again = _run(client.lookup("en", ["k1", "k2"], []))
    assert first == again == {"contracts": {"k1": "Medicinal product"}, "cohesion": {}}
    assert seen == [{"lang": "en", "contract_keys": ["k1", "k2"], "cohesion_ids": []}]
    # Another language is another question.
    _run(client.lookup("de", ["k1"], []))
    assert len(seen) == 2


def test_the_client_splits_a_long_page_into_requests_fontem_api_accepts():
    seen = []
    client = HttpTitleTranslator(base_url="http://api", transport=_transport(seen, body={}))
    _run(client.lookup("en", [f"k{i}" for i in range(MAX_KEYS + 3)], ["Q1"]))
    assert [len(s["contract_keys"]) for s in seen] == [MAX_KEYS, 3]
    assert [s["cohesion_ids"] for s in seen] == [["Q1"], []]


def test_an_unreachable_api_costs_the_translations_not_the_page():
    seen = []
    client = HttpTitleTranslator(base_url="http://api", transport=_transport(seen, status=503))
    assert _run(client.lookup("en", ["k1"], [])) == {"contracts": {}, "cohesion": {}}
    # A failure is not remembered: the next page asks again.
    _run(client.lookup("en", ["k1"], []))
    assert len(seen) == 2


# ── the briefing endpoint ─────────────────────────────────────────────

@pytest.fixture()
def translating(services):
    svc = services["briefing_svc"]
    fake = FakeTranslator()
    svc._translator = fake  # pylint: disable=protected-access
    yield fake
    svc._translator = None  # pylint: disable=protected-access


def test_the_briefing_endpoint_translates_for_the_readers_language(client, services, briefing,
                                                                  translating):
    _, query = briefing
    now = datetime.now(timezone.utc)
    _run(services["feed_repo"].upsert_items([FeedItem(
        query_id=query.id, item_id="cz-1", item_time=now - timedelta(hours=1), nuts=["PT17"],
        rank_value=20_000_000, title="Nemocnice awarded", link="https://fontem.eu/contract/9",
        summary="Léčivý přípravek",
        facets={"kind": "contract", "headline": "Léčivý přípravek"})]))
    translating.found["contracts"] = {"cz-1": "Medicinal product"}
    body = client.get("/briefings/public-investment?nuts=PT&lang=en").json()
    item = next(i for i in body["items"] if i["item_id"] == "cz-1")
    assert item["facets"]["headline"] == "Medicinal product"
    assert item["facets"]["headline_original"] == "Léčivý přípravek"
    assert translating.calls[-1][0] == "en"
    # Without a language (or with one outside the 24) the stored text is shown.
    plain = client.get("/briefings/public-investment?nuts=PT&lang=xx").json()
    item = next(i for i in plain["items"] if i["item_id"] == "cz-1")
    assert item["facets"]["headline"] == "Léčivý přípravek"
