"""Tests for the public data-story search endpoint (GET /data-stories/search)."""
import asyncio
from datetime import datetime, timezone

from src.domain.report import Report
from tests.conftest import _stable_uuid, make_headers, seed_user


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _dt(y, m, d):
    return datetime(y, m, d, tzinfo=timezone.utc)


def _seed(services):
    _run(seed_user(services["user_repo"], "u1"))
    rr = services["report_repo"]
    uid = _stable_uuid("u1")  # created_by uses the derived stable id
    reports = [
        Report(id="r-open", title="Apple orchard subsidies",
               abstract="EU money for fruit", visibility="public_open",
               created_by=uid, created_at=_dt(2023, 5, 1)),
        Report(id="r-auth", title="Apple deals for members",
               abstract="", visibility="public_auth",
               created_by=uid, created_at=_dt(2023, 5, 1)),
        Report(id="r-priv", title="Apple secret dossier",
               abstract="", visibility="private",
               created_by=uid, created_at=_dt(2023, 5, 1)),
        Report(id="r-banana", title="Banana harvest report",
               abstract="nothing here", visibility="public_open",
               created_by=uid, created_at=_dt(2023, 5, 1)),
        Report(id="r-abstract", title="Fruit market overview",
               abstract="deep dive into Apple pricing", visibility="public_open",
               created_by=uid, created_at=_dt(2020, 1, 1)),
    ]
    for r in reports:
        _run(rr.create(r))


def _titles(body):
    return {d["title"] for d in body}


class TestDataStorySearch:
    def test_anonymous_sees_only_public_open_matches(self, client, services):
        _seed(services)
        body = client.get("/data-stories/search?q=apple").json()
        t = _titles(body)
        assert "Apple orchard subsidies" in t
        assert "Fruit market overview" in t          # matched via abstract
        assert "Apple deals for members" not in t     # public_auth hidden from anon
        assert "Apple secret dossier" not in t        # private never
        assert "Banana harvest report" not in t       # non-match

    def test_authenticated_also_sees_public_auth(self, client, services):
        _seed(services)
        body = client.get("/data-stories/search?q=apple",
                          headers=make_headers("u1")).json()
        t = _titles(body)
        assert "Apple deals for members" in t
        assert "Apple orchard subsidies" in t
        assert "Apple secret dossier" not in t        # private still hidden

    def test_abstract_is_searched(self, client, services):
        _seed(services)
        body = client.get("/data-stories/search?q=pricing").json()
        assert _titles(body) == {"Fruit market overview"}

    def test_date_from_filters_by_created_at(self, client, services):
        _seed(services)
        # the abstract match is from 2020; a 2022 lower bound drops it
        body = client.get("/data-stories/search?q=apple&date_from=2022-01-01").json()
        t = _titles(body)
        assert "Apple orchard subsidies" in t
        assert "Fruit market overview" not in t

    def test_private_never_leaks_even_to_author_via_search(self, client, services):
        _seed(services)
        # the public search feed is not a personal listing — even the author
        # doesn't get their private story back from it
        body = client.get("/data-stories/search?q=secret",
                          headers=make_headers("u1")).json()
        assert _titles(body) == set()

    def test_like_wildcards_are_escaped(self, client, services):
        _seed(services)
        # a bare % must not match every story
        body = client.get("/data-stories/search?q=%25").json()  # %25 == '%'
        assert body == []

    def test_empty_query_rejected(self, client, services):
        _seed(services)
        assert client.get("/data-stories/search?q=").status_code == 422
