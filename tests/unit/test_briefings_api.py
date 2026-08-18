"""Briefings: browsing, watching, and the Atom feed."""
# pylint: disable=missing-function-docstring,redefined-outer-name,unused-argument
from __future__ import annotations

import asyncio
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import pytest

from src.domain.feed import FeedItem
from src.domain.named_query import NamedQuery, QueryGroup
from tests.conftest import make_headers, seed_user

MEMBER, OTHER = "member-1", "other-1"
ATOM = "{http://www.w3.org/2005/Atom}"


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture()
def briefing(services):
    """A published query in a public briefing, with items already materialised."""
    _run(seed_user(services["user_repo"], MEMBER, trust_level="contributor"))
    _run(seed_user(services["user_repo"], OTHER, trust_level="contributor"))
    cat, feed = services["named_query_repo"], services["feed_repo"]

    query = _run(cat.create_query(NamedQuery(
        slug="public-contracts", name="Public contracts", lang="cypher",
        status="published", contract_ok=True)))
    group = _run(cat.create_group(QueryGroup(
        slug="public-investment", name="Public investment",
        description="Where public money is committed.", visibility="public")))
    _run(cat.set_group_queries(group.id, [query.id]))
    feed.membership[query.id] = [group.id]
    feed.published.add(query.id)

    now = datetime.now(timezone.utc)
    _run(feed.upsert_items([
        FeedItem(query_id=query.id, item_id="big-pt", item_time=now - timedelta(days=1),
                 nuts=["PT17"], rank_value=11_000_000, title="A big Lisbon contract",
                 link="https://fontem.eu/contract/1", summary="Câmara & Co"),
        FeedItem(query_id=query.id, item_id="small-pt", item_time=now - timedelta(days=1),
                 nuts=["PT16"], rank_value=200_000, title="A small Coimbra contract",
                 link="https://fontem.eu/contract/2"),
        FeedItem(query_id=query.id, item_id="big-es", item_time=now - timedelta(days=2),
                 nuts=["ES300"], rank_value=9_000_000, title="A Madrid contract",
                 link="https://fontem.eu/contract/3"),
    ]))
    return group, query


# ── browsing ────────────────────────────────────────────────────
def test_briefings_are_browsable_anonymously(client, briefing):
    """Deciding whether a briefing is worth watching means seeing inside it,
    and that should not require an account."""
    resp = client.get("/briefings")
    assert resp.status_code == 200
    assert [b["slug"] for b in resp.json()] == ["public-investment"]

    detail = client.get("/briefings/public-investment")
    assert detail.status_code == 200
    assert len(detail.json()["items"]) == 3


def test_a_briefing_with_nothing_published_is_omitted(client, services, briefing):
    cat = services["named_query_repo"]
    empty = _run(cat.create_group(QueryGroup(
        slug="empty", name="Empty", visibility="public")))
    assert empty.id
    assert [b["slug"] for b in client.get("/briefings").json()] == ["public-investment"]


def test_regions_filter_by_prefix(client, briefing):
    """A watcher who picked PT wants PT111 and PT1A0 — prefix, not exact."""
    body = client.get("/briefings/public-investment?nuts=PT").json()
    assert {i["item_id"] for i in body["items"]} == {"big-pt", "small-pt"}

    body = client.get("/briefings/public-investment?nuts=PT16").json()
    assert {i["item_id"] for i in body["items"]} == {"small-pt"}

    body = client.get("/briefings/public-investment?nuts=ES").json()
    assert {i["item_id"] for i in body["items"]} == {"big-es"}


def test_volume_takes_the_highest_ranked(client, briefing):
    """The point of rank_value: asking for one item a week gets the biggest
    one, not an arbitrary one."""
    body = client.get("/briefings/public-investment?nuts=PT&volume=1").json()
    assert [i["item_id"] for i in body["items"]] == ["big-pt"]


def test_an_unknown_briefing_is_404(client, briefing):
    assert client.get("/briefings/nope").status_code == 404


# ── watching ────────────────────────────────────────────────────
def test_watching_requires_a_session(client, briefing):
    assert client.post("/briefings/public-investment/watches",
                       json={}).status_code in (401, 403)


def test_watching_returns_a_feed_url(client, briefing):
    resp = client.post("/briefings/public-investment/watches",
                       json={"nuts": ["PT"], "volume_per_week": 5},
                       headers=make_headers(MEMBER))
    assert resp.status_code == 201
    body = resp.json()
    assert body["nuts"] == ["PT"]
    assert body["volume_per_week"] == 5
    assert body["feed_url"].endswith(".atom")


def test_one_briefing_can_be_watched_at_several_scopes(client, briefing):
    """The case an earlier model made impossible: fifty a week from Coimbra,
    ten from Portugal, ten from the whole EU — one briefing, three
    subscriptions, because a local award and a European one are different
    kinds of news to the same reader."""
    made = []
    for nuts, volume in (['PT16'], 50), (['PT'], 10), (['EU'], 10):
        r = client.post("/briefings/public-investment/watches",
                        json={"nuts": nuts, "volume_per_week": volume},
                        headers=make_headers(MEMBER))
        assert r.status_code == 201, r.text
        made.append(r.json())

    assert len({w["id"] for w in made}) == 3
    # Three independent subscriptions means three feed URLs.
    assert len({w["feed_url"] for w in made}) == 3
    assert len(client.get("/me/watches", headers=make_headers(MEMBER)).json()) == 3


def test_an_exact_duplicate_is_a_double_click_not_a_second_feed(client, briefing):
    first = client.post("/briefings/public-investment/watches",
                        json={"nuts": ["PT"], "volume_per_week": 10},
                        headers=make_headers(MEMBER)).json()
    again = client.post("/briefings/public-investment/watches",
                        json={"nuts": ["PT"], "volume_per_week": 10},
                        headers=make_headers(MEMBER)).json()
    assert again["id"] == first["id"]
    assert len(client.get("/me/watches", headers=make_headers(MEMBER)).json()) == 1


def test_a_watch_is_adjusted_by_id_and_keeps_its_feed_url(client, briefing):
    """Someone's reader is already polling that URL; changing the scope of a
    feed is not a reason to break it."""
    watch = client.post("/briefings/public-investment/watches",
                        json={"nuts": ["PT"], "volume_per_week": 10},
                        headers=make_headers(MEMBER)).json()
    resp = client.patch(f"/me/watches/{watch['id']}",
                        json={"volume_per_week": 25}, headers=make_headers(MEMBER))
    assert resp.status_code == 200
    assert resp.json()["volume_per_week"] == 25
    assert resp.json()["nuts"] == ["PT"]        # untouched
    assert resp.json()["feed_url"] == watch["feed_url"]


def test_adjusting_someone_elses_watch_is_refused(client, briefing):
    watch = client.post("/briefings/public-investment/watches", json={},
                        headers=make_headers(MEMBER)).json()
    assert client.patch(f"/me/watches/{watch['id']}", json={"volume_per_week": 3},
                        headers=make_headers(OTHER)).status_code == 403


def test_everywhere_subsumes_any_other_selection(client, briefing):
    body = client.post("/briefings/public-investment/watches",
                       json={"nuts": ["EU", "PT"]}, headers=make_headers(MEMBER)).json()
    assert body["nuts"] == ["EU"]


def test_a_nonsense_region_is_rejected(client, briefing):
    resp = client.post("/briefings/public-investment/watches",
                       json={"nuts": ["'; DROP TABLE"]}, headers=make_headers(MEMBER))
    assert resp.status_code == 400


def test_volume_is_bounded(client, briefing):
    for volume in (0, 5000):
        resp = client.post("/briefings/public-investment/watches",
                           json={"volume_per_week": volume}, headers=make_headers(MEMBER))
        assert resp.status_code == 422, volume


def test_a_watch_belongs_to_its_owner(client, briefing):
    watch = client.post("/briefings/public-investment/watches", json={},
                        headers=make_headers(MEMBER)).json()
    assert client.delete(f"/me/watches/{watch['id']}",
                         headers=make_headers(OTHER)).status_code == 403
    assert client.delete(f"/me/watches/{watch['id']}",
                         headers=make_headers(MEMBER)).status_code == 204
    assert client.get("/me/watches", headers=make_headers(MEMBER)).json() == []


# ── the Atom feed ───────────────────────────────────────────────
def _feed_url(client, nuts=None, volume=None):
    body = {"nuts": nuts or ["EU"]}
    if volume:
        body["volume_per_week"] = volume
    watch = client.post("/briefings/public-investment/watches", json=body,
                        headers=make_headers(MEMBER)).json()
    return "/capi".join(watch["feed_url"].split("/capi")[1:]) or watch["feed_url"]


def test_the_feed_is_valid_atom_and_needs_no_login(client, briefing):
    """Atom readers cannot authenticate, which is the whole reason the URL
    carries a token."""
    path = _feed_url(client)
    resp = client.get(path)          # no Authorization header
    assert resp.status_code == 200
    assert "application/atom+xml" in resp.headers["content-type"]

    root = ET.fromstring(resp.text)
    assert root.tag == f"{ATOM}feed"
    entries = root.findall(f"{ATOM}entry")
    assert len(entries) == 3
    assert all(e.find(f"{ATOM}id") is not None for e in entries)
    assert all(e.find(f"{ATOM}updated") is not None for e in entries)


def test_the_feed_escapes_what_it_renders(client, briefing):
    """One unescaped ampersand makes a feed unparseable for every reader at
    once, and these titles carry company names."""
    resp = client.get(_feed_url(client))
    assert "Câmara &amp; Co" in resp.text
    ET.fromstring(resp.text)         # would raise if malformed


def test_the_feed_honours_the_watchers_regions(client, briefing):
    resp = client.get(_feed_url(client, nuts=["PT16"]))
    root = ET.fromstring(resp.text)
    titles = [e.find(f"{ATOM}title").text for e in root.findall(f"{ATOM}entry")]
    assert titles == ["A small Coimbra contract"]


def test_a_polite_reader_gets_a_304(client, briefing):
    """A reader polling every 15 minutes should cost a 304, not a document."""
    path = _feed_url(client)
    first = client.get(path)
    etag = first.headers["etag"]
    again = client.get(path, headers={"If-None-Match": etag})
    assert again.status_code == 304
    assert again.headers["etag"] == etag


def test_an_unknown_token_is_404_not_500(client, briefing):
    assert client.get("/feeds/not-a-real-token.atom").status_code == 404


def test_revoking_a_watch_kills_its_feed(client, briefing):
    path = _feed_url(client)
    watch_id = client.get("/me/watches", headers=make_headers(MEMBER)).json()[0]["id"]
    assert client.get(path).status_code == 200
    client.delete(f"/me/watches/{watch_id}", headers=make_headers(MEMBER))
    assert client.get(path).status_code == 404


def test_a_briefing_exposes_the_id_a_watch_refers_to(client, briefing):
    """A watch names a group_id. Without the id here, a client holding both
    lists cannot say which briefing it watches without a round trip each."""
    listed = client.get("/briefings").json()[0]
    watch = client.post("/briefings/public-investment/watches", json={},
                        headers=make_headers(MEMBER)).json()
    assert listed["id"] == watch["group_id"]


def test_overlapping_watches_each_get_their_own_scope(client, briefing):
    """Coimbra and Portugal overlap, and that is fine: each feed answers its
    own question. Collapsing them was the bug."""
    coimbra = client.post("/briefings/public-investment/watches",
                          json={"nuts": ["PT16"], "volume_per_week": 50},
                          headers=make_headers(MEMBER)).json()
    portugal = client.post("/briefings/public-investment/watches",
                           json={"nuts": ["PT"], "volume_per_week": 10},
                           headers=make_headers(MEMBER)).json()

    def feed_titles(watch):
        token = watch["feed_url"].rsplit("/", 1)[-1].removesuffix(".atom")
        body = client.get(f"/feeds/{token}.atom").text
        return [line for line in body.splitlines() if "<title>" in line]

    # The Coimbra feed sees the Coimbra item; the Portugal feed sees both.
    assert any("Coimbra" in t for t in feed_titles(coimbra))
    assert not any("Lisbon" in t for t in feed_titles(coimbra))
    portugal_titles = feed_titles(portugal)
    assert any("Coimbra" in t for t in portugal_titles)
    assert any("Lisbon" in t for t in portugal_titles)


def test_a_reader_cannot_hoard_watches_without_limit(client, briefing, services):
    """Each watch is a query against the items table on every page load."""
    from src.services.briefing_service import MAX_WATCHES  # pylint: disable=import-outside-toplevel
    from src.domain.feed import Watch  # pylint: disable=import-outside-toplevel
    feed = services["feed_repo"]
    user = _run(seed_user(services["user_repo"], "hoarder", trust_level="contributor"))
    for i in range(MAX_WATCHES):
        _run(feed.create_watch(Watch(user_id=user.id, group_id="g", nuts=[f"X{i}"],
                                     volume_per_week=1, token=f"t{i}")))
    resp = client.post("/briefings/public-investment/watches",
                       json={"nuts": ["FR"], "volume_per_week": 3},
                       headers=make_headers("hoarder"))
    assert resp.status_code == 400
    assert str(MAX_WATCHES) in resp.json()["detail"]
