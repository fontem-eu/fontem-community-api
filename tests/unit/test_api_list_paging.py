"""The two list endpoints the load canary caught: paged by default.

The DAST account accumulates an investigation and a studio project per scan,
on purpose, so that slowness shows up there before users meet it. It did:
GET /studio/projects was 1,147 projects and 3.26 MB — fetched by the nav rail
on every page of the app — and GET /investigations was 1,149 rows and 436 KB.
Both now page by default: thirty projects, ten investigations.

The earlier investigations paging tests create five rows, which a default of
ten never cuts, so they would pass whatever the default was. These create
more than a page on purpose.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from src.api.paging import decode_before
from tests.conftest import make_headers, seed_user


def _seed(services, *names):
    async def go():
        for n in names:
            await seed_user(services["user_repo"], n)
    asyncio.get_event_loop().run_until_complete(go())


def _cursor(row: dict) -> str:
    return f'{row["updated_at"]}|{row["id"]}'


def _walk(client, path: str, headers: dict, **params) -> list[list[dict]]:
    """Every page, following the cursor until a short page."""
    pages, before = [], ""
    limit = params.get("limit")
    while True:
        query = {**params, **({"before": before} if before else {})}
        page = client.get(path, params=query, headers=headers).json()
        pages.append(page)
        if limit is None or len(page) < limit or not page:
            return pages
        before = _cursor(page[-1])


class TestCursor:
    def test_round_trips_what_the_api_serialises(self):
        when = datetime(2026, 9, 25, 0, 21, 2, 72626, tzinfo=timezone.utc)
        assert decode_before(f"{when.isoformat()}|abc") == (when, "abc")
        assert decode_before("2026-09-25T00:21:02.072626Z|abc") == (when, "abc")

    def test_anything_unusable_means_the_first_page(self):
        for raw in ("", "not-a-cursor", "|abc", "2026-09-25T00:21:02Z|", "yesterday|abc"):
            assert decode_before(raw) is None


class TestInvestigationsDefaultPage:
    def test_a_bare_request_gets_ten_and_the_cursor_gets_the_rest(self, client, services):
        _seed(services, "user-1")
        h = make_headers("user-1")
        for i in range(12):
            assert client.post("/investigations", json={"name": f"inv-{i}"}, headers=h).status_code == 201

        first = client.get("/investigations", headers=h).json()
        assert len(first) == 10
        rest = client.get("/investigations", params={"before": _cursor(first[-1])}, headers=h).json()
        assert len(rest) == 2
        assert {r["id"] for r in first}.isdisjoint(r["id"] for r in rest)

    def test_a_picker_can_still_read_every_investigation(self, client, services):
        """Three views pick from ALL of a user's investigations. There is no
        unbounded form any more, so they page at the cap — and must see all."""
        _seed(services, "user-1")
        h = make_headers("user-1")
        for i in range(12):
            client.post("/investigations", json={"name": f"inv-{i}"}, headers=h)
        pages = _walk(client, "/investigations", h, limit=500)
        assert sum(len(p) for p in pages) == 12


class TestProjectsPaging:
    def test_a_bare_request_gets_thirty_newest_first(self, client, services):
        _seed(services, "user-1")
        h = make_headers("user-1")
        for i in range(33):
            assert client.post("/studio/projects", json={"name": f"p{i}"}, headers=h).status_code == 201

        first = client.get("/studio/projects", headers=h).json()
        assert len(first) == 30
        keys = [(p["updated_at"], p["id"]) for p in first]
        assert keys == sorted(keys, reverse=True)
        rest = client.get("/studio/projects", params={"before": _cursor(first[-1])}, headers=h).json()
        assert len(rest) == 3

    def test_pages_cover_everything_once(self, client, services):
        _seed(services, "user-1")
        h = make_headers("user-1")
        created = {client.post("/studio/projects", json={"name": f"p{i}"}, headers=h).json()["id"]
                   for i in range(7)}
        pages = _walk(client, "/studio/projects", h, limit=3)
        assert [len(p) for p in pages] == [3, 3, 1]
        seen = [p["id"] for page in pages for p in page]
        assert len(seen) == len(set(seen)) and set(seen) == created

    def test_ties_on_updated_at_neither_repeat_nor_vanish(self, client, services):
        """Ordering on updated_at alone — what the list did before — is not a
        total order. Five projects saved in the same instant, walked two at a
        time, must still come back exactly once each."""
        _seed(services, "user-1")
        h = make_headers("user-1")
        ids = [client.post("/studio/projects", json={"name": f"t{i}"}, headers=h).json()["id"]
               for i in range(5)]
        same = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
        store = services["data_project_repo"]._projects  # pylint: disable=protected-access
        for pid in ids:
            store[pid].updated_at = same
        seen = [p["id"] for page in _walk(client, "/studio/projects", h, limit=2) for p in page]
        assert sorted(seen) == sorted(ids)

    def test_the_investigation_view_pages_too(self, client, services):
        _seed(services, "user-1")
        h = make_headers("user-1")
        inv = client.post("/investigations", json={"name": "I"}, headers=h).json()["id"]
        for i in range(4):
            client.post("/studio/projects", json={"name": f"p{i}", "investigation_id": inv}, headers=h)
        page = client.get("/studio/projects", params={"investigation_id": inv, "limit": 3}, headers=h).json()
        assert len(page) == 3 and all(p["my_access"]["level"] == "owner" for p in page)
        more = client.get("/studio/projects",
                          params={"investigation_id": inv, "limit": 3, "before": _cursor(page[-1])},
                          headers=h).json()
        assert len(more) == 1

    def test_page_bounds(self, client, services):
        _seed(services, "user-1")
        h = make_headers("user-1")
        client.post("/studio/projects", json={"name": "a"}, headers=h)
        assert client.get("/studio/projects", params={"limit": 0}, headers=h).status_code == 422
        assert client.get("/studio/projects", params={"limit": 201}, headers=h).status_code == 422
        garbled = client.get("/studio/projects", params={"before": "not-a-cursor"}, headers=h)
        assert garbled.status_code == 200 and len(garbled.json()) == 1

    def test_someone_elses_projects_never_page_in(self, client, services):
        _seed(services, "user-1", "user-2")
        for i in range(3):
            client.post("/studio/projects", json={"name": f"theirs{i}"}, headers=make_headers("user-2"))
        mine = client.post("/studio/projects", json={"name": "mine"}, headers=make_headers("user-1")).json()
        pages = _walk(client, "/studio/projects", make_headers("user-1"), limit=1)
        assert [p["id"] for page in pages for p in page] == [mine["id"]]
