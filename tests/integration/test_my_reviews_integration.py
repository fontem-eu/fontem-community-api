"""My reviews against real Postgres: what a page costs, and its cursor.

The list used to build every row on its own — the review's reviewers, its
article, both revisions of a change, three to five statements a review — for
every review the person had ever started. The e2e account keeps them all,
and on 2026-10-09 its 1,513 reviews took 5,595 statements and 22 seconds,
which failed the promotion gate. Each statement was fast (well under a
millisecond on the server); there were simply thousands of them.
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.engine import Engine

from tests.integration.conftest import make_headers

MY_REVIEWS = "/data-stories/my-reviews"


def _doc(text):
    return {"type": "doc", "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": text}]},
    ]}


def _article(client, h) -> tuple[str, str]:
    rid = client.post("/data-stories", json={"title": "R"}, headers=h).json()["id"]
    first = client.put(f"/data-stories/{rid}/content",
                       json={"tiptap": _doc("published")}, headers=h).json()["revision"]
    return rid, first


def _proposal(client, h, rid, base, text) -> str:
    client.put(f"/data-stories/{rid}/content",
               json={"tiptap": _doc(text), "base_revision": base}, headers=h)
    return client.post(f"/data-stories/{rid}/reviews", json={}, headers=h).json()["id"]


def _reads(client, h, rid, n) -> list[str]:
    return [client.post(f"/data-stories/{rid}/reviews", json={"kind": "article"},
                        headers=h).json()["id"] for _ in range(n)]


class _Statements:
    """Counts every statement sent to Postgres while open, by any engine."""

    def __init__(self):
        self.n = 0

    def _count(self, *_args, **_kwargs):
        self.n += 1

    def __enter__(self):
        sa.event.listen(Engine, "before_cursor_execute", self._count)
        return self

    def __exit__(self, *_exc):
        sa.event.remove(Engine, "before_cursor_execute", self._count)


def _cost_of_my_reviews(client, h) -> tuple[int, int]:
    """(statements, rows) for one bare request, after one to warm up, so
    nothing a first request does once — a sign-in record, a cache — is
    counted on one side and not the other."""
    client.get(MY_REVIEWS, headers=h)
    with _Statements() as statements:
        rows = client.get(MY_REVIEWS, headers=h).json()
    return statements.n, len(rows)


def _one_of_each(client, h, reader_id) -> None:
    """A published proposal, an open one somebody was asked to read, and
    two reads: every kind of row the page shows."""
    rid, first = _article(client, h)
    published = _proposal(client, h, rid, first, "first change")
    client.post(f"/data-stories/{rid}/reviews/{published}/publish", headers=h)
    head = client.get(f"/data-stories/{rid}", headers=h).json()["head_revision"]
    open_one = _proposal(client, h, rid, head, "second change")
    client.post(f"/data-stories/{rid}/reviews/{open_one}/reviewers",
                json={"user_id": reader_id}, headers=h)
    _reads(client, h, rid, 2)


def _more_history(client, h, articles: int) -> None:
    """Per article: a published proposal and three reads."""
    for _ in range(articles):
        rid, base = _article(client, h)
        done = _proposal(client, h, rid, base, "a change")
        client.post(f"/data-stories/{rid}/reviews/{done}/publish", headers=h)
        _reads(client, h, rid, 3)


class TestMyReviewsCost:
    def test_a_page_costs_the_same_however_long_the_history(
        self, client, user_id, user2_id,
    ):
        """Every kind of row on the page, and then many more of them: the
        page must cost the same number of statements either way."""
        h = make_headers(user_id)
        client.get(MY_REVIEWS, headers=make_headers(user2_id))  # exists, so can be invited
        _one_of_each(client, h, user2_id)
        few, few_rows = _cost_of_my_reviews(client, h)

        _more_history(client, h, articles=4)
        many, many_rows = _cost_of_my_reviews(client, h)

        assert (few_rows, many_rows) == (4, 20)
        assert many == few, f"{few} statements for {few_rows} reviews, {many} for {many_rows}"


class TestMyReviewsCursorOnPostgres:
    """The keyset cursor against a real timestamptz column and uuid ids.

    The unit tests page the in-memory repository, which compares Python
    tuples. Only Postgres shows whether the ``updated_at`` the API returns
    parses back into a value that ``(updated_at, id) < (...)`` compares
    correctly — a lost microsecond would repeat or skip a row at every
    page boundary.
    """

    def test_the_cursor_walks_every_review_exactly_once_ties_included(
        self, client, user_id, _postgres,
    ):
        h = make_headers(user_id)
        rid, _ = _article(client, h)
        ids = _reads(client, h, rid, 7)
        # Four of them touched in the same instant, set in the database
        # itself, so only the id tie-break in the SQL keeps them apart.
        url = _postgres.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")
        engine = sa.create_engine(url)
        with engine.begin() as conn:
            conn.execute(
                sa.text("UPDATE reviews SET updated_at = '2026-10-09T13:37:00+00' "
                        "WHERE id = ANY(CAST(:ids AS uuid[]))"),
                {"ids": ids[:4]},
            )
        engine.dispose()

        seen, before = [], ""
        for _ in range(10):
            params = {"limit": 2, **({"before": before} if before else {})}
            page = client.get(MY_REVIEWS, params=params, headers=h).json()
            seen += [r["id"] for r in page]
            if len(page) < 2:
                break
            before = f'{page[-1]["updated_at"]}|{page[-1]["id"]}'
        assert sorted(seen) == sorted(ids)

    def test_a_cursor_whose_id_is_not_a_uuid_gets_the_first_page(self, client, user_id):
        """The id half is compared with a uuid column; passed through, a
        garbled one is a Postgres type error and a 400. Like any cursor
        the server cannot use, it means the first page."""
        h = make_headers(user_id)
        rid, _ = _article(client, h)
        ids = _reads(client, h, rid, 2)
        resp = client.get(MY_REVIEWS, params={"before": "2026-10-09T13:37:00+00:00|abc"},
                          headers=h)
        assert resp.status_code == 200
        assert sorted(r["id"] for r in resp.json()) == sorted(ids)
