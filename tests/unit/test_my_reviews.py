"""My reviews: a page at a time, and each row is what the review says.

GET /data-stories/my-reviews returned every review a person had ever started
or been asked to read, and nothing leaves that list. The e2e account keeps
every review it starts, and by 2026-10-09 it had 1,513 of them: the page took
22 seconds to answer and failed the promotion gate. It now pages by default,
thirty at a time, on the same ``before=<updated_at>|<id>`` contract as the
other lists (src/api/paging.py).

These create more than a page on purpose; a test with three reviews passes
whatever the default is.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from tests.conftest import _stable_uuid, make_headers, seed_user

MY_REVIEWS = "/data-stories/my-reviews"


def _doc(text):
    return {"type": "doc", "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": text}]},
    ]}


def _seed(services, *names):
    async def go():
        for n in names:
            await seed_user(services["user_repo"], n)
    asyncio.get_event_loop().run_until_complete(go())


def _article(client, h, title="R"):
    rid = client.post("/data-stories", json={"title": title}, headers=h).json()["id"]
    client.put(f"/data-stories/{rid}/content", json={"tiptap": _doc("The lead.")}, headers=h)
    return rid


def _read(client, h, rid, n):
    """Ask for ``n`` reads of one article; several may be open at once."""
    return [client.post(f"/data-stories/{rid}/reviews", json={"kind": "article"},
                        headers=h).json()["id"] for _ in range(n)]


def _cursor(row: dict) -> str:
    return f'{row["updated_at"]}|{row["id"]}'


def _walk(client, h, limit: int) -> list[list[dict]]:
    """Every page, following the cursor until a short page — or a page with
    nothing new, so a list that ignores the cursor cannot loop this."""
    pages, before, seen = [], "", set()
    while True:
        params = {"limit": limit, **({"before": before} if before else {})}
        page = client.get(MY_REVIEWS, params=params, headers=h).json()
        pages.append(page)
        fresh = {r["id"] for r in page} - seen
        seen |= fresh
        if len(page) < limit or not fresh:
            return pages
        before = _cursor(page[-1])


class TestMyReviewsPaging:
    def test_a_bare_request_gets_thirty_most_recent_first(self, client, services):
        _seed(services, "user-1")
        h = make_headers("user-1")
        _read(client, h, _article(client, h), 33)

        first = client.get(MY_REVIEWS, headers=h).json()
        assert len(first) == 30
        keys = [(r["updated_at"], r["id"]) for r in first]
        assert keys == sorted(keys, reverse=True)
        rest = client.get(MY_REVIEWS, params={"before": _cursor(first[-1])}, headers=h).json()
        assert len(rest) == 3
        assert {r["id"] for r in first}.isdisjoint(r["id"] for r in rest)

    def test_pages_cover_every_review_once(self, client, services):
        _seed(services, "user-1")
        h = make_headers("user-1")
        made = set(_read(client, h, _article(client, h), 7))

        pages = _walk(client, h, limit=3)
        assert [len(p) for p in pages] == [3, 3, 1]
        seen = [r["id"] for page in pages for r in page]
        assert len(seen) == len(set(seen)) and set(seen) == made

    def test_ties_on_updated_at_neither_repeat_nor_vanish(self, client, services):
        """Ordering on updated_at alone, as the list did, is not a total
        order: five reviews touched in the same instant, walked two at a
        time, must each come back exactly once."""
        _seed(services, "user-1")
        h = make_headers("user-1")
        ids = _read(client, h, _article(client, h), 5)
        same = datetime(2026, 10, 9, 13, 37, tzinfo=timezone.utc)
        store = services["report_repo"]._reviews  # pylint: disable=protected-access
        for rid in ids:
            store[rid].updated_at = same

        seen = [r["id"] for page in _walk(client, h, limit=2) for r in page]
        assert sorted(seen) == sorted(ids)

    def test_the_list_is_still_both_what_i_started_and_what_i_was_asked_to_read(
        self, client, services,
    ):
        _seed(services, "user-1", "user-2")
        h1, h2 = make_headers("user-1"), make_headers("user-2")
        rid = _article(client, h1)
        asked = _read(client, h1, rid, 1)[0]
        client.post(f"/data-stories/{rid}/reviews/{asked}/reviewers",
                    json={"user_id": _stable_uuid("user-2")}, headers=h1)
        own = _read(client, h2, _article(client, h2, title="Theirs"), 1)[0]

        rows = {r["id"]: r for r in client.get(MY_REVIEWS, headers=h2).json()}
        assert set(rows) == {asked, own}
        assert rows[asked]["mine"] is False and rows[asked]["report_title"] == "R"
        assert rows[own]["mine"] is True and rows[own]["report_title"] == "Theirs"


class TestMyReviewsRow:
    def test_a_row_says_what_the_review_itself_says(self, client, services):
        """The list builds its rows from reads shared across the page; the
        review page reads each thing for itself. An open proposal with
        somebody invited to read it, and a read of the article beside it,
        must say the same in both places."""
        _seed(services, "user-1", "user-2")
        h = make_headers("user-1")
        rid = client.post("/data-stories", json={"title": "R"}, headers=h).json()["id"]
        first = client.put(f"/data-stories/{rid}/content",
                           json={"tiptap": _doc("published")}, headers=h).json()["revision"]
        client.put(f"/data-stories/{rid}/content",
                   json={"tiptap": _doc("proposed"), "base_revision": first}, headers=h)
        proposal = client.post(f"/data-stories/{rid}/reviews", json={}, headers=h).json()["id"]
        client.post(f"/data-stories/{rid}/reviews/{proposal}/reviewers",
                    json={"user_id": _stable_uuid("user-2")}, headers=h)
        read = _read(client, h, rid, 1)[0]

        rows = {r["id"]: r for r in client.get(MY_REVIEWS, headers=h).json()}
        for review_id in (proposal, read):
            review = client.get(f"/data-stories/{rid}/reviews/{review_id}", headers=h).json()
            for key in ("state", "kind", "changes", "reviewers", "behind", "can_publish",
                        "source_head", "target_base", "updated_at"):
                assert rows[review_id][key] == review[key], (review["kind"], key)
        assert rows[proposal]["changes"] and rows[proposal]["can_publish"] is True
        assert rows[proposal]["reviewers"] == [_stable_uuid("user-2")]
        assert rows[read]["changes"] == {} and rows[read]["reviewers"] == []
