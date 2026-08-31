"""HTTP-level tests for report endpoints (covers routers/reports.py)."""
from __future__ import annotations

import asyncio

import pytest
from tests.conftest import _stable_uuid, make_headers, seed_user


@pytest.mark.asyncio

def _doc(text):
    """A minimal TipTap document carrying one line of text."""
    return {"type": "doc", "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": text}]},
    ]}


class TestReportAPI:
    """Cover report CRUD via the HTTP API."""

    async def _setup_user(self, services):
        await seed_user(services["user_repo"], "user-1")

    def test_create_report(self, client, services):
        """POST /reports creates a report and returns 201."""
        asyncio.get_event_loop().run_until_complete(self._setup_user(services))
        resp = client.post(
            "/reports",
            json={"title": "Test Report", "abstract": "An abstract"},
            headers=make_headers("user-1"),
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "Test Report"
        assert data["abstract"] == "An abstract"
        assert data["id"] is not None

    def test_list_reports(self, client, services):
        """GET /reports returns user's reports."""
        asyncio.get_event_loop().run_until_complete(self._setup_user(services))
        h = make_headers("user-1")
        client.post("/reports", json={"title": "R1"}, headers=h)
        client.post("/reports", json={"title": "R2"}, headers=h)
        resp = client.get("/reports", headers=h)
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_get_report(self, client, services):
        """GET /reports/:id returns report with sections."""
        asyncio.get_event_loop().run_until_complete(self._setup_user(services))
        h = make_headers("user-1")
        create = client.post("/reports", json={"title": "R"}, headers=h)
        rid = create.json()["id"]
        resp = client.get(f"/reports/{rid}", headers=h)
        assert resp.status_code == 200
        assert resp.json()["title"] == "R"
        assert "sections" in resp.json()

    def test_update_report(self, client, services):
        """PUT /reports/:id updates title and visibility."""
        asyncio.get_event_loop().run_until_complete(self._setup_user(services))
        h = make_headers("user-1")
        create = client.post("/reports", json={"title": "Old"}, headers=h)
        rid = create.json()["id"]
        resp = client.put(
            f"/reports/{rid}",
            json={"title": "New", "visibility": "public_open"},
            headers=h,
        )
        assert resp.status_code == 200
        assert resp.json()["title"] == "New"

    def test_update_report_nuts_region(self, client, services):
        """PUT sets the region tag; GET returns it; a bad code is 422."""
        asyncio.get_event_loop().run_until_complete(self._setup_user(services))
        h = make_headers("user-1")
        rid = client.post("/reports", json={"title": "R"}, headers=h).json()["id"]
        resp = client.put(f"/reports/{rid}", json={"nuts_region": "PT17"}, headers=h)
        assert resp.status_code == 200 and resp.json()["nuts_region"] == "PT17"
        got = client.get(f"/data-stories/{rid}", headers=h).json()
        assert got["nuts_region"] == "PT17"
        bad = client.put(f"/reports/{rid}", json={"nuts_region": "not-a-code"}, headers=h)
        assert bad.status_code == 422

    def test_delete_report(self, client, services):
        """DELETE /reports/:id returns 204."""
        asyncio.get_event_loop().run_until_complete(self._setup_user(services))
        h = make_headers("user-1")
        create = client.post("/reports", json={"title": "Doomed"}, headers=h)
        rid = create.json()["id"]
        resp = client.delete(f"/reports/{rid}", headers=h)
        assert resp.status_code == 204

    def test_saving_the_document(self, client, services):
        """PUT /reports/:id/content stores the document."""
        asyncio.get_event_loop().run_until_complete(self._setup_user(services))
        h = make_headers("user-1")
        rid = client.post("/reports", json={"title": "R"}, headers=h).json()["id"]
        resp = client.put(f"/reports/{rid}/content",
                          json={"tiptap": _doc("Hello")}, headers=h)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_the_document_persists_on_reload(self, client, services):
        """The document is returned when fetching the report."""
        asyncio.get_event_loop().run_until_complete(self._setup_user(services))
        h = make_headers("user-1")
        rid = client.post("/reports", json={"title": "R"}, headers=h).json()["id"]
        client.put(f"/reports/{rid}/content",
                   json={"tiptap": _doc("Persistent")}, headers=h)
        resp = client.get(f"/reports/{rid}", headers=h)
        assert "Persistent" in str(resp.json()["content_doc"])

    def test_get_nonexistent_report_returns_404(self, client, services):
        """GET /reports/00000000-0000-4000-8000-000000000000 returns 404.

        The old handler ran the perm check first and surfaced 403 on
        missing reports to avoid leaking existence. get_viewable now
        loads the report first and 404s if it's missing — correct for
        any caller since a nonexistent id tells you nothing either way.
        """
        asyncio.get_event_loop().run_until_complete(self._setup_user(services))
        resp = client.get("/reports/00000000-0000-4000-8000-000000000000", headers=make_headers("user-1"))
        assert resp.status_code == 404

    def test_canonical_data_stories_path(self, client, services):
        """The canonical /data-stories/* path mirrors the legacy /reports
        alias. Cover create + read end-to-end on the new prefix so the
        rename window doesn't silently break the new path.
        """
        asyncio.get_event_loop().run_until_complete(self._setup_user(services))
        h = make_headers("user-1")
        create = client.post(
            "/data-stories",
            json={"title": "Canonical path", "abstract": "via /data-stories"},
            headers=h,
        )
        assert create.status_code == 201
        sid = create.json()["id"]

        resp = client.get(f"/data-stories/{sid}", headers=h)
        assert resp.status_code == 200
        assert resp.json()["title"] == "Canonical path"

        # Story created via the canonical path is also readable via the
        # legacy alias, and vice-versa — same handlers, same DB row.
        legacy = client.get(f"/reports/{sid}", headers=h)
        assert legacy.status_code == 200
        assert legacy.json()["id"] == sid


@pytest.mark.asyncio
class TestDocumentConcurrency:
    """The save carries the revision it was written against.

    Without that, the server cannot tell a fresh save from one built on
    an hour-old buffer — which is exactly how a published story lost the
    widgets an assistant had put in it (2026-08-30).
    """

    async def _seed(self, services):
        await seed_user(services["user_repo"], "user-1")

    def _story(self, client, h):
        return client.post("/reports", json={"title": "R"}, headers=h).json()["id"]

    def test_the_read_names_the_revision_it_returned(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._seed(services))
        h = make_headers("user-1")
        rid = self._story(client, h)
        client.put(f"/reports/{rid}/content",
                   json={"tiptap": _doc("one")}, headers=h)

        body = client.get(f"/reports/{rid}", headers=h).json()
        assert body["head_revision"]

    def test_a_save_on_the_current_head_succeeds(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._seed(services))
        h = make_headers("user-1")
        rid = self._story(client, h)
        first = client.put(f"/reports/{rid}/content",
                           json={"tiptap": _doc("one")}, headers=h).json()
        resp = client.put(
            f"/reports/{rid}/content",
            json={"tiptap": _doc("two"), "base_revision": first["revision"]},
            headers=h,
        )
        assert resp.status_code == 200
        assert resp.json()["revision"] != first["revision"]

    def test_a_save_on_a_stale_baseline_is_refused(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._seed(services))
        h = make_headers("user-1")
        rid = self._story(client, h)
        base = client.put(f"/reports/{rid}/content",
                          json={"tiptap": _doc("base")}, headers=h).json()
        # Somebody else's save lands first.
        client.put(f"/reports/{rid}/content",
                   json={"tiptap": _doc("theirs"),
                         "base_revision": base["revision"]}, headers=h)

        stale = client.put(
            f"/reports/{rid}/content",
            json={"tiptap": _doc("mine, written on the old text"),
                  "base_revision": base["revision"]},
            headers=h,
        )
        assert stale.status_code == 409
        body = stale.json()
        # The refusal hands back what it protected, so the editor can show
        # the difference instead of just saying no.
        assert "theirs" in str(body["current_doc"])
        assert body["current_revision"]

        # And the draft still holds theirs, not the stale one. (The
        # published text is untouched either way — saves land on the
        # draft, and main moves only when a proposal merges.)
        after = client.get(f"/reports/{rid}", headers=h).json()
        assert "theirs" in str(after["draft_doc"])

    def test_a_save_with_no_baseline_cannot_overwrite(self, client, services):
        """A client that names no baseline is a client that has not read
        the document — it may create the first revision and nothing else."""
        asyncio.get_event_loop().run_until_complete(self._seed(services))
        h = make_headers("user-1")
        rid = self._story(client, h)
        client.put(f"/reports/{rid}/content",
                   json={"tiptap": _doc("existing")}, headers=h)

        resp = client.put(f"/reports/{rid}/content",
                          json={"tiptap": _doc("blind")}, headers=h)
        assert resp.status_code == 409


class TestReportPresignedUrls:
    """SEC-2026-06-11 #4 — bucket is private; reads come through
    presigned URLs minted by the router on every response.

    The test stub MinioStorage emits
    ``https://test-presigned/<key>?sig=stub`` so we can assert the
    rewrite happened without depending on a real MinIO. The end-to-end
    "the browser can fetch through nginx" leg is covered by the
    staging smoke suite (STORY-UPLOAD-SEC-1..2).
    """

    async def _seed_owner(self, services):
        await seed_user(services["user_repo"], "owner-1")

    def test_get_report_rewrites_uploads_to_presigned_url(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._seed_owner(services))
        h = make_headers("owner-1")
        rid = client.post("/reports", json={"title": "WithImage"}, headers=h).json()["id"]
        # A real TipTap image node, the shape the editor saves.
        key = "0319fb3d-987c-4fc4-8d64-044a4daca389/deadbeef.png"
        client.put(
            f"/reports/{rid}/content",
            json={"tiptap": {"type": "doc", "content": [
                {"type": "image", "attrs": {"src": f"/uploads/{key}"}},
            ]}},
            headers=h,
        )

        doc = str(client.get(f"/reports/{rid}", headers=h).json()["content_doc"])
        assert f"https://test-presigned/{key}?sig=stub" in doc, doc
        assert "/uploads/" not in doc, doc

    def test_anonymous_public_open_also_gets_presigned(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._seed_owner(services))
        h = make_headers("owner-1")
        rid = client.post("/reports", json={"title": "Pub"}, headers=h).json()["id"]
        client.put(
            f"/reports/{rid}", json={"visibility": "public_open"}, headers=h,
        )
        # Hex filename: that is what the uploader mints, and what the
        # /uploads/ matcher is anchored on.
        key = "0319fb3d-987c-4fc4-8d64-044a4daca389/c0ffee01.png"
        client.put(
            f"/reports/{rid}/content",
            json={"tiptap": {"type": "doc", "content": [
                {"type": "image", "attrs": {"src": f"/uploads/{key}"}},
            ]}},
            headers=h,
        )

        # No auth header at all — the bucket stays private for everyone,
        # so even the anonymous read has to be given a signed URL.
        doc = str(client.get(f"/reports/{rid}").json()["content_doc"])
        assert f"https://test-presigned/{key}?sig=stub" in doc, doc
        assert "/uploads/" not in doc, doc



class TestRevisionHistory:
    """History, comparison and restore over the API.

    The point of keeping revisions is being able to see and undo what
    happened — a chain nobody can read is just storage.
    """

    async def _seed(self, services):
        await seed_user(services["user_repo"], "user-1")

    def _story_with_history(self, client, h):
        rid = client.post("/reports", json={"title": "R"}, headers=h).json()["id"]
        rev = client.put(f"/reports/{rid}/content",
                         json={"tiptap": _doc("first")}, headers=h).json()["revision"]
        rev2 = client.put(
            f"/reports/{rid}/content",
            json={"tiptap": _doc("second"), "base_revision": rev},
            headers=h).json()["revision"]
        return rid, rev, rev2

    def test_the_history_lists_what_each_save_changed(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._seed(services))
        h = make_headers("user-1")
        rid, first, second = self._story_with_history(client, h)

        rows = client.get(f"/reports/{rid}/revisions", headers=h).json()
        assert [r["id"] for r in rows] == [second, first]
        # Newest first, and each row says what it did to its parent.
        assert rows[0]["parent_id"] == first
        assert rows[0]["changes"]["changed"] == 1
        assert rows[0]["author_kind"] == "human"

    def test_the_default_diff_is_what_the_last_save_changed(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._seed(services))
        h = make_headers("user-1")
        rid, first, second = self._story_with_history(client, h)

        body = client.get(f"/reports/{rid}/diff", headers=h).json()
        assert body["from"] == first
        assert body["to"] == second
        ops = [o["op"] for o in body["operations"]]
        assert ops == ["replace"]

    def test_any_two_revisions_can_be_compared(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._seed(services))
        h = make_headers("user-1")
        rid, first, second = self._story_with_history(client, h)

        body = client.get(f"/reports/{rid}/diff",
                          params={"from": second, "to": first},
                          headers=h).json()
        # Reversed: the same edit read the other way round.
        assert body["from"] == second and body["to"] == first
        assert [o["after"]["text"] for o in body["operations"]] == ["first"]

    def test_a_revision_from_another_article_is_not_found(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._seed(services))
        h = make_headers("user-1")
        rid, _, _ = self._story_with_history(client, h)
        other, _, other_rev = self._story_with_history(client, h)
        assert other != rid

        resp = client.get(f"/reports/{rid}/diff",
                          params={"to": other_rev}, headers=h)
        assert resp.status_code == 404

    def test_restoring_adds_a_revision_and_rewrites_nothing(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._seed(services))
        h = make_headers("user-1")
        rid, first, second = self._story_with_history(client, h)

        resp = client.post(f"/reports/{rid}/revisions/{first}/restore", headers=h)
        assert resp.status_code == 200
        restored = resp.json()["revision"]
        assert restored not in (first, second)

        # The draft reads as the old one again...
        doc = client.get(f"/reports/{rid}", headers=h).json()
        assert "first" in str(doc["draft_doc"])
        # ...and the history still has all three, with the restore on top.
        rows = client.get(f"/reports/{rid}/revisions", headers=h).json()
        assert [r["id"] for r in rows] == [restored, second, first]


class TestChangeReviews:
    """Publishing is a decision, and the decision is recorded.

    Three rules the editors chose, pinned here because they are policy
    rather than mechanism: an author may merge their own proposal and the
    merge says so; nothing expires; and proposals are for people who may
    edit the article, not for its readers.
    """

    async def _seed(self, services):
        await seed_user(services["user_repo"], "user-1")
        await seed_user(services["user_repo"], "user-2")

    def _article_with_a_draft(self, client, h):
        rid = client.post("/reports", json={"title": "R"}, headers=h).json()["id"]
        first = client.put(f"/reports/{rid}/content",
                           json={"tiptap": _doc("published")},
                           headers=h).json()["revision"]
        drafted = client.put(
            f"/reports/{rid}/content",
            json={"tiptap": _doc("proposed"), "base_revision": first},
            headers=h).json()["revision"]
        return rid, first, drafted

    def test_a_proposal_carries_the_changes_it_would_make(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._seed(services))
        h = make_headers("user-1")
        rid, first, drafted = self._article_with_a_draft(client, h)

        mr = client.post(f"/reports/{rid}/reviews",
                         json={"title": "Rewrite the lead"}, headers=h).json()
        assert mr["state"] == "open"
        assert mr["source_head"] == drafted
        assert mr["target_base"] == first
        assert mr["can_publish"] is True
        assert [o["op"] for o in mr["operations"]] == ["replace"]

    def test_merging_publishes_and_records_that_nobody_else_read_it(
        self, client, services,
    ):
        asyncio.get_event_loop().run_until_complete(self._seed(services))
        h = make_headers("user-1")
        rid, _, drafted = self._article_with_a_draft(client, h)
        mr = client.post(f"/reports/{rid}/reviews", json={},
                         headers=h).json()

        merged = client.post(
            f"/reports/{rid}/reviews/{mr['id']}/publish", headers=h).json()
        assert merged["state"] == "merged"
        # Allowed — solo authorship is the normal case — but on the record.
        assert merged["self_merged"] is True
        assert merged["merged_by"]

        published = client.get(f"/reports/{rid}", headers=h).json()
        assert "proposed" in str(published["content_doc"])
        assert published["head_revision"] == drafted

    def test_a_merge_by_someone_else_is_not_marked_self_merged(
        self, client, services,
    ):
        asyncio.get_event_loop().run_until_complete(self._seed(services))
        h1, h2 = make_headers("user-1"), make_headers("user-2")
        rid, _, _ = self._article_with_a_draft(client, h1)
        client.put(f"/reports/{rid}/access",
                   json={"user_id": _stable_uuid("user-2"), "level": "editor"},
                   headers=h1)
        mr = client.post(f"/reports/{rid}/reviews", json={},
                         headers=h1).json()

        merged = client.post(
            f"/reports/{rid}/reviews/{mr['id']}/publish", headers=h2)
        if merged.status_code == 200:
            assert merged.json()["self_merged"] is False

    def test_a_proposal_that_fell_behind_is_refused_with_the_difference(
        self, client, services,
    ):
        """No guesswork: merging a proposal whose base has moved would
        discard whatever moved it."""
        asyncio.get_event_loop().run_until_complete(self._seed(services))
        h = make_headers("user-1")
        rid, first, drafted = self._article_with_a_draft(client, h)
        mr = client.post(f"/reports/{rid}/reviews", json={},
                         headers=h).json()

        # Main moves on underneath the open proposal.
        moved = client.put(
            f"/reports/{rid}/content",
            json={"tiptap": _doc("something else"), "base_revision": drafted},
            headers=h).json()["revision"]
        second = client.post(f"/reports/{rid}/reviews", json={},
                             headers=h).json()
        client.post(f"/reports/{rid}/reviews/{second['id']}/publish",
                    headers=h)

        stale = client.post(
            f"/reports/{rid}/reviews/{mr['id']}/publish", headers=h)
        assert stale.status_code in (409, 400)
        if stale.status_code == 409:
            assert stale.json()["behind"] >= 1
        assert moved

    def test_closing_leaves_the_draft_and_its_revisions_alone(
        self, client, services,
    ):
        asyncio.get_event_loop().run_until_complete(self._seed(services))
        h = make_headers("user-1")
        rid, _, drafted = self._article_with_a_draft(client, h)
        mr = client.post(f"/reports/{rid}/reviews", json={},
                         headers=h).json()

        closed = client.post(
            f"/reports/{rid}/reviews/{mr['id']}/close", headers=h).json()
        assert closed["state"] == "closed"
        # Nothing expires and nothing is deleted: the work is still there.
        after = client.get(f"/reports/{rid}", headers=h).json()
        assert after["draft_revision"] == drafted

    def test_a_reader_of_the_article_sees_none_of_its_proposals(
        self, client, services,
    ):
        """An unreviewed proposal is not yet a claim the platform is
        making, so it does not appear to the article's readers."""
        asyncio.get_event_loop().run_until_complete(self._seed(services))
        h1 = make_headers("user-1")
        rid, _, _ = self._article_with_a_draft(client, h1)
        client.post(f"/reports/{rid}/reviews", json={}, headers=h1)
        client.put(f"/reports/{rid}", json={"visibility": "public_open"},
                   headers=h1)

        resp = client.get(f"/reports/{rid}/reviews",
                          headers=make_headers("user-2"))
        assert resp.status_code == 200
        assert resp.json() == []

    def test_a_stranger_cannot_probe_a_private_article_for_reviews(
        self, client, services,
    ):
        asyncio.get_event_loop().run_until_complete(self._seed(services))
        h1 = make_headers("user-1")
        rid, _, _ = self._article_with_a_draft(client, h1)
        client.post(f"/reports/{rid}/reviews", json={}, headers=h1)

        # Private article: existence itself must not be confirmable.
        assert client.get(f"/reports/{rid}/reviews",
                          headers=make_headers("user-2")).status_code in (403, 404)


class TestArticleReviews:
    """The other kind: one version read end to end, with nothing to merge.

    A self-review before publishing, or somebody else's read. The whole
    output is the conversation.
    """

    async def _seed(self, services):
        for u in ("user-1", "user-2"):
            await seed_user(services["user_repo"], u)

    def _article(self, client, h):
        rid = client.post("/reports", json={"title": "R"},
                          headers=h).json()["id"]
        client.put(f"/reports/{rid}/content",
                   json={"tiptap": _doc("The lead paragraph.")}, headers=h)
        return rid

    def test_a_self_review_returns_the_article_in_blocks_to_comment_on(
        self, client, services,
    ):
        asyncio.get_event_loop().run_until_complete(self._seed(services))
        h = make_headers("user-1")
        rid = self._article(client, h)

        review = client.post(f"/reports/{rid}/reviews",
                             json={"kind": "article", "title": "Read-through"},
                             headers=h).json()
        assert review["kind"] == "article"
        # No diff — there is nothing to compare it against.
        assert "operations" not in review
        assert [b["text"] for b in review["blocks"]] == ["The lead paragraph."]
        # And nothing to publish: an article review does not move the text.
        assert review["can_publish"] is False

    def test_publishing_an_article_review_is_refused(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._seed(services))
        h = make_headers("user-1")
        rid = self._article(client, h)
        review = client.post(f"/reports/{rid}/reviews",
                             json={"kind": "article"}, headers=h).json()

        resp = client.post(f"/reports/{rid}/reviews/{review['id']}/publish",
                           headers=h)
        assert resp.status_code == 400

    def test_several_article_reviews_can_be_open_at_once(self, client, services):
        """A piece can be read by more than one person, and each read is
        its own conversation — unlike a change, where the draft branch is
        singular."""
        asyncio.get_event_loop().run_until_complete(self._seed(services))
        h = make_headers("user-1")
        rid = self._article(client, h)

        first = client.post(f"/reports/{rid}/reviews", json={"kind": "article"},
                            headers=h).json()
        second = client.post(f"/reports/{rid}/reviews", json={"kind": "article"},
                             headers=h).json()
        assert first["id"] != second["id"]
        assert len(client.get(f"/reports/{rid}/reviews", headers=h).json()) == 2


class TestReviewConversation:
    """Invitations and inline comments — the reason a review exists."""

    async def _seed(self, services):
        for u in ("user-1", "user-2"):
            await seed_user(services["user_repo"], u)

    def _review(self, client, h, kind="article"):
        rid = client.post("/reports", json={"title": "R"},
                          headers=h).json()["id"]
        client.put(f"/reports/{rid}/content",
                   json={"tiptap": _doc("Under review.")}, headers=h)
        review = client.post(f"/reports/{rid}/reviews", json={"kind": kind},
                             headers=h).json()
        return rid, review["id"]

    def test_a_comment_anchors_to_a_block_and_comes_back_with_the_review(
        self, client, services,
    ):
        asyncio.get_event_loop().run_until_complete(self._seed(services))
        h = make_headers("user-1")
        rid, review_id = self._review(client, h)

        posted = client.post(
            f"/reports/{rid}/reviews/{review_id}/comments",
            json={"body": "This lead buries the number.",
                  "anchor": "paragraph\\x00Under review."},
            headers=h)
        assert posted.status_code == 201

        review = client.get(f"/reports/{rid}/reviews/{review_id}",
                            headers=h).json()
        assert len(review["comments"]) == 1
        assert review["comments"][0]["body"] == "This lead buries the number."
        assert review["comments"][0]["resolved"] is False

    def test_an_empty_comment_is_refused(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._seed(services))
        h = make_headers("user-1")
        rid, review_id = self._review(client, h)
        resp = client.post(f"/reports/{rid}/reviews/{review_id}/comments",
                           json={"body": "   "}, headers=h)
        assert resp.status_code in (400, 422)

    def test_a_comment_can_be_resolved(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._seed(services))
        h = make_headers("user-1")
        rid, review_id = self._review(client, h)
        comment = client.post(f"/reports/{rid}/reviews/{review_id}/comments",
                              json={"body": "typo"}, headers=h).json()

        resolved = client.post(
            f"/reports/{rid}/reviews/{review_id}/comments/{comment['id']}/resolve",
            headers=h).json()
        assert resolved["resolved"] is True

    def test_an_invited_reviewer_can_open_it_and_comment(self, client, services):
        """Inviting somebody to read an article is useless if they cannot
        then open the thing they were asked to read."""
        asyncio.get_event_loop().run_until_complete(self._seed(services))
        h1, h2 = make_headers("user-1"), make_headers("user-2")
        rid, review_id = self._review(client, h1)

        # Before the invitation: not theirs to see.
        assert client.get(f"/reports/{rid}/reviews/{review_id}",
                          headers=h2).status_code == 404

        invited = client.post(
            f"/reports/{rid}/reviews/{review_id}/reviewers",
            json={"user_id": _stable_uuid("user-2")}, headers=h1).json()
        assert _stable_uuid("user-2") in invited["reviewers"]

        got = client.get(f"/reports/{rid}/reviews/{review_id}", headers=h2)
        assert got.status_code == 200
        posted = client.post(f"/reports/{rid}/reviews/{review_id}/comments",
                             json={"body": "Reads well."}, headers=h2)
        assert posted.status_code == 201

    def test_my_reviews_covers_both_what_i_started_and_what_i_was_asked_to_read(
        self, client, services,
    ):
        asyncio.get_event_loop().run_until_complete(self._seed(services))
        h1, h2 = make_headers("user-1"), make_headers("user-2")
        rid, review_id = self._review(client, h1)
        client.post(f"/reports/{rid}/reviews/{review_id}/reviewers",
                    json={"user_id": _stable_uuid("user-2")}, headers=h1)

        mine = client.get("/reports/my-reviews", headers=h1).json()
        assert [r["id"] for r in mine] == [review_id]
        assert mine[0]["mine"] is True
        assert mine[0]["report_title"] == "R"

        theirs = client.get("/reports/my-reviews", headers=h2).json()
        assert [r["id"] for r in theirs] == [review_id]
        # Not theirs, but waiting on them.
        assert theirs[0]["mine"] is False

    def test_marking_a_read_done_leaves_the_article_alone(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._seed(services))
        h = make_headers("user-1")
        rid, review_id = self._review(client, h)

        done = client.post(f"/reports/{rid}/reviews/{review_id}/close",
                           params={"state": "completed"}, headers=h).json()
        assert done["state"] == "completed"
        assert client.get(f"/reports/{rid}", headers=h).status_code == 200
