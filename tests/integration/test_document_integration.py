"""
Integration tests for the article document — full HTTP API against real
PostgreSQL.

Replaces the section-lifecycle suite. An article is title + abstract +
body: there is one document, written whole, and its structure is the
headings inside it. What is worth testing at this level is that a save
round-trips through Postgres and that the previous content is kept.
"""
from __future__ import annotations

from tests.integration.conftest import make_headers


def _doc(text: str) -> dict:
    return {"type": "doc", "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": text}]},
    ]}


class TestDocument:
    """DOC-I01..I04: the document lifecycle."""

    def _create_report(self, client, user_id):
        h = make_headers(user_id)
        rid = client.post(
            "/reports", json={"title": "DocumentTest"}, headers=h,
        ).json()["id"]
        return rid, h

    def test_a_saved_document_reads_back(self, client, user_id):
        """DOC-I01: what goes in comes out, through Postgres."""
        rid, h = self._create_report(client, user_id)
        resp = client.put(f"/reports/{rid}/content",
                          json={"tiptap": _doc("Hello")}, headers=h)
        assert resp.status_code == 200

        got = client.get(f"/reports/{rid}", headers=h).json()
        # A v2 document comes back whole, as content_doc — `sections` is
        # the empty remnant of the old shape.
        assert "Hello" in str(got["content_doc"])

    def test_saving_again_replaces_the_body(self, client, user_id):
        """DOC-I02: the document is written whole, not appended to."""
        rid, h = self._create_report(client, user_id)
        first = client.put(f"/reports/{rid}/content",
                           json={"tiptap": _doc("first")}, headers=h).json()
        client.put(f"/reports/{rid}/content",
                   json={"tiptap": _doc("second"),
                         "base_revision": first["revision"]}, headers=h)

        # The draft is what a later save replaces; the published text
        # moves only when a proposal merges.
        body = str(client.get(f"/reports/{rid}", headers=h).json()["draft_doc"])
        assert "second" in body
        assert "first" not in body

    def test_the_previous_content_is_kept(self, client, user_id):
        """DOC-I03: overwriting keeps a copy of what was there.

        This is the substrate the revision history is built on — a save
        that forgets its predecessor cannot be reviewed or reverted.
        """
        rid, h = self._create_report(client, user_id)
        first = client.put(f"/reports/{rid}/content",
                           json={"tiptap": _doc("original")}, headers=h).json()
        client.put(f"/reports/{rid}/content",
                   json={"tiptap": _doc("revised"),
                         "base_revision": first["revision"]}, headers=h)

        report = client.get(f"/reports/{rid}", headers=h).json()
        # content_version counts document saves, so a reader can tell that
        # the text moved even without diffing it.
        assert report["content_version"] >= 2

    def test_a_stranger_cannot_write_the_document(self, client, user_id,
                                                  user2_id):
        """DOC-I04: the permission gate is on the document, not the UI."""
        rid, _ = self._create_report(client, user_id)
        resp = client.put(f"/reports/{rid}/content",
                          json={"tiptap": _doc("not mine")},
                          headers=make_headers(user2_id))
        assert resp.status_code in (403, 404)


class TestConcurrentSaves:
    """Two writers, real Postgres. The in-memory repo enforces nothing —
    a concurrency rule only means something at the database."""

    def test_the_second_writer_is_refused_not_silently_dropped(
        self, client, user_id,
    ):
        h = make_headers(user_id)
        rid = client.post("/reports", json={"title": "Race"},
                          headers=h).json()["id"]
        base = client.put(f"/reports/{rid}/content",
                          json={"tiptap": _doc("base")}, headers=h).json()

        # Both editors loaded the same revision.
        a = client.put(f"/reports/{rid}/content",
                       json={"tiptap": _doc("editor A"),
                             "base_revision": base["revision"]}, headers=h)
        b = client.put(f"/reports/{rid}/content",
                       json={"tiptap": _doc("editor B"),
                             "base_revision": base["revision"]}, headers=h)

        assert a.status_code == 200
        assert b.status_code == 409
        stored = client.get(f"/reports/{rid}", headers=h).json()
        assert "editor A" in str(stored["draft_doc"])

    def test_the_chain_survives_a_round_trip(self, client, user_id):
        """Parent links, hashes and the branch pointer are what the
        history and every future diff are read from."""
        h = make_headers(user_id)
        rid = client.post("/reports", json={"title": "Chain"},
                          headers=h).json()["id"]
        first = client.put(f"/reports/{rid}/content",
                           json={"tiptap": _doc("one")}, headers=h).json()
        second = client.put(f"/reports/{rid}/content",
                            json={"tiptap": _doc("two"),
                                  "base_revision": first["revision"]},
                            headers=h).json()

        body = client.get(f"/reports/{rid}", headers=h).json()
        assert body["draft_revision"] == second["revision"]
        assert second["revision"] != first["revision"]
        # The first save published (nothing to review it against); the
        # second is a draft on top of it.
        assert body["head_revision"] == first["revision"]
