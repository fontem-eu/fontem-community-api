"""HTTP-level tests for story translations (routers/reports.py).

The contract under test:
 - a story has one original (title/abstract/document in report.language)
   and any number of translations keyed by two-letter lang;
 - saving the original's document or changing its title/abstract bumps
   content_version, which flips every translation to outdated=true;
 - saving or resolving a translation pins it to the current version
   (outdated=false);
 - reads follow story visibility (anonymous can read a public story's
   translations); writes require edit rights.
"""
from __future__ import annotations

import asyncio

import pytest
from tests.conftest import make_headers, seed_user

DOC = {"tiptap": {"type": "doc", "content": [
    {"type": "paragraph", "content": [{"type": "text", "text": "original body"}]}]},
    "version": 2}
DOC_PT = {"tiptap": {"type": "doc", "content": [
    {"type": "paragraph", "content": [{"type": "text", "text": "corpo traduzido"}]}]},
    "version": 2}


@pytest.mark.asyncio
class TestTranslationAPI:
    async def _setup_users(self, services):
        await seed_user(services["user_repo"], "user-1")
        await seed_user(services["user_repo"], "user-2")

    def _mk_story(self, client, h, visibility="private"):
        create = client.post("/reports", json={"title": "Original title"}, headers=h)
        rid = create.json()["id"]
        client.put(f"/reports/{rid}", json={"visibility": visibility}, headers=h)
        self._save(client, h, rid, DOC)
        return rid

    @staticmethod
    def _save(client, h, rid, doc):
        """Save onto the caller's draft, naming the revision it was
        written against — the server refuses a save that does not."""
        body = client.get(f"/reports/{rid}", headers=h).json()
        base = body.get("draft_revision") or body["head_revision"]
        return client.put(f"/reports/{rid}/content",
                          json={**doc, "base_revision": base}, headers=h)

    @classmethod
    def _publish(cls, client, h, rid, doc):
        """Save and then merge, which is what changes the PUBLISHED text.

        A translation goes stale when the article readers see changes —
        not when its author saves a draft. That distinction is the whole
        point of the draft/merge split, so these tests go through it.
        """
        cls._save(client, h, rid, doc)
        mr = client.post(f"/reports/{rid}/merge-requests",
                         json={"title": "edit"}, headers=h).json()
        return client.post(
            f"/reports/{rid}/merge-requests/{mr['id']}/merge", headers=h)

    @staticmethod
    def _edited(text):
        """A document that differs — an identical save is now a no-op, so
        a test that means "the author edited the article" has to edit it."""
        return {"tiptap": {"type": "doc", "content": [
            {"type": "paragraph",
             "content": [{"type": "text", "text": text}]}]}}

    def test_upsert_and_get_translation(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._setup_users(services))
        h = make_headers("user-1")
        rid = self._mk_story(client, h)
        resp = client.put(
            f"/reports/{rid}/translations/pt",
            json={"title": "Título", "abstract": "Resumo", "tiptap": DOC_PT["tiptap"]},
            headers=h,
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        got = client.get(f"/reports/{rid}/translations/pt", headers=h)
        assert got.status_code == 200
        body = got.json()
        assert body["title"] == "Título"
        assert body["abstract"] == "Resumo"
        assert body["content_doc"]["tiptap"]["content"][0]["content"][0]["text"] == "corpo traduzido"
        assert body["outdated"] is False

    def test_translation_summary_rides_story_get(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._setup_users(services))
        h = make_headers("user-1")
        rid = self._mk_story(client, h)
        client.put(f"/reports/{rid}/translations/pt",
                   json={"title": "T", "tiptap": DOC_PT["tiptap"]}, headers=h)
        got = client.get(f"/reports/{rid}", headers=h)
        assert got.json()["language"] == "en"
        assert got.json()["translations"] == [{"lang": "pt", "outdated": False}]

    def test_original_edit_flags_translation_outdated(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._setup_users(services))
        h = make_headers("user-1")
        rid = self._mk_story(client, h)
        client.put(f"/reports/{rid}/translations/pt",
                   json={"title": "T", "tiptap": DOC_PT["tiptap"]}, headers=h)
        # edit the ORIGINAL document -> translation becomes outdated
        self._publish(client, h, rid, self._edited("body, revised"))
        got = client.get(f"/reports/{rid}/translations/pt", headers=h)
        assert got.json()["outdated"] is True
        # metadata list agrees
        lst = client.get(f"/reports/{rid}/translations", headers=h)
        assert lst.json()["translations"][0]["outdated"] is True

    def test_title_change_flags_outdated_but_visibility_does_not(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._setup_users(services))
        h = make_headers("user-1")
        rid = self._mk_story(client, h)
        client.put(f"/reports/{rid}/translations/de",
                   json={"title": "T", "tiptap": DOC_PT["tiptap"]}, headers=h)
        # visibility-only change: NOT translatable content
        client.put(f"/reports/{rid}", json={"visibility": "public_open"}, headers=h)
        got = client.get(f"/reports/{rid}/translations/de", headers=h)
        assert got.json()["outdated"] is False
        # title change: translatable
        client.put(f"/reports/{rid}", json={"title": "New original title"}, headers=h)
        got = client.get(f"/reports/{rid}/translations/de", headers=h)
        assert got.json()["outdated"] is True

    def test_resolve_clears_outdated_without_editing(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._setup_users(services))
        h = make_headers("user-1")
        rid = self._mk_story(client, h)
        client.put(f"/reports/{rid}/translations/pt",
                   json={"title": "T", "tiptap": DOC_PT["tiptap"]}, headers=h)
        self._publish(client, h, rid, self._edited("original moved on"))
        assert client.get(f"/reports/{rid}/translations/pt", headers=h).json()["outdated"] is True

        resp = client.post(f"/reports/{rid}/translations/pt/resolve", headers=h)
        assert resp.status_code == 200
        got = client.get(f"/reports/{rid}/translations/pt", headers=h)
        assert got.json()["outdated"] is False
        assert got.json()["title"] == "T"  # text untouched

    def test_re_saving_translation_clears_outdated(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._setup_users(services))
        h = make_headers("user-1")
        rid = self._mk_story(client, h)
        client.put(f"/reports/{rid}/translations/pt",
                   json={"title": "T v1", "tiptap": DOC_PT["tiptap"]}, headers=h)
        client.put(f"/reports/{rid}/content", json=DOC, headers=h)
        client.put(f"/reports/{rid}/translations/pt",
                   json={"title": "T v2", "tiptap": DOC_PT["tiptap"]}, headers=h)
        got = client.get(f"/reports/{rid}/translations/pt", headers=h)
        assert got.json()["outdated"] is False
        assert got.json()["title"] == "T v2"

    def test_anonymous_reads_public_story_translation(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._setup_users(services))
        h = make_headers("user-1")
        rid = self._mk_story(client, h, visibility="public_open")
        client.put(f"/reports/{rid}/translations/fr",
                   json={"title": "Titre", "tiptap": DOC_PT["tiptap"]}, headers=h)
        got = client.get(f"/reports/{rid}/translations/fr")  # no auth header
        assert got.status_code == 200
        assert got.json()["title"] == "Titre"
        lst = client.get(f"/reports/{rid}/translations")
        assert lst.status_code == 200
        assert lst.json()["language"] == "en"

    def test_anonymous_cannot_read_private_translation(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._setup_users(services))
        h = make_headers("user-1")
        rid = self._mk_story(client, h)  # private
        client.put(f"/reports/{rid}/translations/fr",
                   json={"title": "Titre", "tiptap": DOC_PT["tiptap"]}, headers=h)
        got = client.get(f"/reports/{rid}/translations/fr")
        assert got.status_code == 404  # existence not leaked

    def test_non_editor_cannot_write_or_resolve(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._setup_users(services))
        h1 = make_headers("user-1")
        h2 = make_headers("user-2")
        rid = self._mk_story(client, h1, visibility="public_open")
        client.put(f"/reports/{rid}/translations/pt",
                   json={"title": "T", "tiptap": DOC_PT["tiptap"]}, headers=h1)
        put = client.put(f"/reports/{rid}/translations/es",
                         json={"title": "X", "tiptap": DOC_PT["tiptap"]}, headers=h2)
        assert put.status_code in (403, 404)
        res = client.post(f"/reports/{rid}/translations/pt/resolve", headers=h2)
        assert res.status_code in (403, 404)
        dele = client.delete(f"/reports/{rid}/translations/pt", headers=h2)
        assert dele.status_code in (403, 404)

    def test_delete_translation(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._setup_users(services))
        h = make_headers("user-1")
        rid = self._mk_story(client, h)
        client.put(f"/reports/{rid}/translations/pt",
                   json={"title": "T", "tiptap": DOC_PT["tiptap"]}, headers=h)
        resp = client.delete(f"/reports/{rid}/translations/pt", headers=h)
        assert resp.status_code == 204
        assert client.get(f"/reports/{rid}/translations/pt", headers=h).status_code == 404

    def test_language_field_on_story(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._setup_users(services))
        h = make_headers("user-1")
        rid = self._mk_story(client, h)
        resp = client.put(f"/reports/{rid}", json={"language": "hu"}, headers=h)
        assert resp.status_code == 200
        assert resp.json()["language"] == "hu"
        # bad lang rejected at the schema layer
        bad = client.put(f"/reports/{rid}", json={"language": "hungarian"}, headers=h)
        assert bad.status_code == 422

    def test_invalid_lang_path_is_422(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._setup_users(services))
        h = make_headers("user-1")
        rid = self._mk_story(client, h)
        resp = client.put(f"/reports/{rid}/translations/portuguese",
                          json={"title": "T", "tiptap": DOC_PT["tiptap"]}, headers=h)
        assert resp.status_code == 422

    def test_missing_translation_404(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._setup_users(services))
        h = make_headers("user-1")
        rid = self._mk_story(client, h)
        assert client.get(f"/reports/{rid}/translations/sv", headers=h).status_code == 404
        assert client.post(f"/reports/{rid}/translations/sv/resolve", headers=h).status_code == 404

    # ── feed overlay: lists show the reader's language ──────────

    def test_list_shows_translated_title_and_abstract_for_lang(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._setup_users(services))
        h = make_headers("user-1")
        rid = self._mk_story(client, h, visibility="public_open")
        client.put(f"/reports/{rid}/translations/pt",
                   json={"title": "Título traduzido", "abstract": "Resumo",
                         "tiptap": DOC_PT["tiptap"]}, headers=h)

        # scope=mine
        mine = client.get("/reports?scope=mine&lang=pt", headers=h).json()
        card = next(c for c in mine if c["id"] == rid)
        assert card["title"] == "Título traduzido"
        assert card["abstract"] == "Resumo"
        assert card["original_title"] == "Original title"
        assert card["translation_lang"] == "pt"
        assert card["translation_outdated"] is False

        # scope=public, anonymous
        pub = client.get("/reports?scope=public&lang=pt").json()
        card = next(c for c in pub if c["id"] == rid)
        assert card["title"] == "Título traduzido"

    def test_list_without_lang_or_without_translation_keeps_original(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._setup_users(services))
        h = make_headers("user-1")
        rid = self._mk_story(client, h)
        client.put(f"/reports/{rid}/translations/pt",
                   json={"title": "Título", "tiptap": DOC_PT["tiptap"]}, headers=h)
        # no lang -> original
        card = next(c for c in client.get("/reports?scope=mine", headers=h).json()
                    if c["id"] == rid)
        assert card["title"] == "Original title"
        assert "translation_lang" not in card
        # lang without a matching translation -> original
        card = next(c for c in client.get("/reports?scope=mine&lang=de", headers=h).json()
                    if c["id"] == rid)
        assert card["title"] == "Original title"
        # lang equal to the original's language -> original (no self-overlay)
        card = next(c for c in client.get("/reports?scope=mine&lang=en", headers=h).json()
                    if c["id"] == rid)
        assert card["title"] == "Original title"

    def test_list_marks_outdated_translations(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._setup_users(services))
        h = make_headers("user-1")
        rid = self._mk_story(client, h)
        client.put(f"/reports/{rid}/translations/pt",
                   json={"title": "Título", "tiptap": DOC_PT["tiptap"]}, headers=h)
        self._publish(client, h, rid, self._edited("original moved on"))
        card = next(c for c in client.get("/reports?scope=mine&lang=pt", headers=h).json()
                    if c["id"] == rid)
        assert card["title"] == "Título"  # still shown, but…
        assert card["translation_outdated"] is True
