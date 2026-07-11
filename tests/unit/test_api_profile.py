"""Profile API: anonymous GET /users/{id}/profile, authed PUT /users/me/profile
(link normalisation), and avatar upload."""
from __future__ import annotations

import asyncio
import json
from io import BytesIO

from PIL import Image

from src.domain.activity import ActivityEvent
from src.domain.report import Report
from tests.conftest import _stable_uuid, make_headers, seed_user


class TestProfileAPI:
    async def _seed(self, services):
        await seed_user(services["user_repo"], "u1")
        await services["report_repo"].create(Report(
            id="pub1", title="Money trail",
            visibility="public_open", created_by=_stable_uuid("u1")))

    def test_put_me_then_anonymous_get_profile(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._seed(services))
        h = make_headers("u1")
        put = client.put(
            "/users/me/profile",
            json={"summary": "Follows the money.",
                  "links": [{"name": "Site", "url": "https://s.io"},
                            {"name": "Bad", "url": "javascript:alert(1)"}]},
            headers=h,
        )
        assert put.status_code == 200, put.text
        assert put.json()["summary"] == "Follows the money."
        # javascript: link rejected by validation
        assert put.json()["links"] == [{"name": "Site", "url": "https://s.io"}]

        resp = client.get(f"/users/{_stable_uuid('u1')}/profile")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["summary"] == "Follows the money."
        assert body["links"] == [{"name": "Site", "url": "https://s.io"}]
        assert any(a["title"] == "Money trail" for a in body["articles"])

    def test_put_normalises_schemeless_link(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._seed(services))
        h = make_headers("u1")
        put = client.put(
            "/users/me/profile",
            json={"summary": "", "links": [{"name": "LI", "url": "linkedin.com/in/x"}]},
            headers=h,
        )
        assert put.status_code == 200, put.text
        # schemeless URL kept + normalised to https (the reported bug)
        assert put.json()["links"] == [{"name": "LI", "url": "https://linkedin.com/in/x"}]

    def test_avatar_upload_sets_and_presigns_avatar(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._seed(services))
        h = make_headers("u1")
        # a genuinely valid PNG so the sanitise pipeline (magic sniff +
        # re-encode) accepts it
        buf = BytesIO()
        Image.new("RGB", (2, 2), (200, 100, 50)).save(buf, "PNG")
        png = buf.getvalue()
        up = client.post(
            "/users/me/avatar",
            files={"file": ("a.png", png, "image/png")},
            headers=h,
        )
        assert up.status_code == 200, up.text
        assert up.json()["avatar_url"].startswith("https://test-presigned/")
        # profile GET returns a presigned avatar (stored /uploads ref rewritten)
        prof = client.get(f"/users/{_stable_uuid('u1')}/profile").json()
        assert prof["avatar_url"].startswith("https://test-presigned/")

    def test_get_profile_unknown_user_404(self, client):
        resp = client.get(f"/users/{_stable_uuid('ghost')}/profile")
        assert resp.status_code == 404, resp.text


class TestProfileSecurity:
    """No PII / private data leaks on the public profile endpoint."""

    async def _seed(self, services):
        await seed_user(services["user_repo"], "u1")
        await seed_user(services["user_repo"], "u2")
        rr = services["report_repo"]
        await rr.create(Report(id="pub1", title="PUBLIC-TITLE",
                               visibility="public_open", created_by=_stable_uuid("u1")))
        await rr.create(Report(id="priv1", title="SECRET-PRIVATE",
                               visibility="private", created_by=_stable_uuid("u1")))
        ar = services["activity_repo"]
        for eid, et, summ in [("pub1", "story", "PUBLIC-TITLE"),
                              ("priv1", "story", "SECRET-PRIVATE"),
                              ("d1", "dossier", "SECRET-DOSSIER")]:
            await ar.record(ActivityEvent(
                actor_id=_stable_uuid("u1"), entity_type=et, entity_id=eid,
                action="created", summary=summ))

    def _profile(self, client, headers=None):
        return client.get(f"/users/{_stable_uuid('u1')}/profile", headers=headers or {}).json()

    def test_account_email_never_leaks_when_using_a_custom_email(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._seed(services))
        # owner opts to display a DIFFERENT public email
        client.put("/users/me/profile", headers=make_headers("u1"), json={
            "summary": "", "links": [], "show_email": True,
            "use_custom_email": True, "custom_email": "public@shown.io"})
        # owner view: gets the editable settings + account email
        owner = self._profile(client, make_headers("u1"))
        assert owner["account_email"] == "u1@test.com"
        assert owner["show_email"] is True and owner["custom_email"] == "public@shown.io"
        # anonymous + other user: only the custom address, NEVER the account one
        for hdr in (None, make_headers("u2")):
            body = self._profile(client, hdr)
            assert body["email"] == "public@shown.io"
            for leaked in ("account_email", "show_email", "use_custom_email", "custom_email"):
                assert leaked not in body, leaked
            assert "u1@test.com" not in json.dumps(body)

    def test_email_hidden_when_display_unchecked(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._seed(services))
        client.put("/users/me/profile", headers=make_headers("u1"),
                   json={"summary": "", "links": [], "show_email": False,
                         "use_custom_email": True, "custom_email": "hidden@x.io"})
        for hdr in (None, make_headers("u2")):
            body = self._profile(client, hdr)
            assert body["email"] == ""
            assert "u1@test.com" not in json.dumps(body)
            assert "hidden@x.io" not in json.dumps(body)

    def test_activity_never_leaks_private_entity_titles(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._seed(services))
        # owner sees the full feed
        owner = self._profile(client, make_headers("u1"))
        owner_titles = {e["summary"] for e in owner["recent_activity"]}
        assert {"PUBLIC-TITLE", "SECRET-PRIVATE", "SECRET-DOSSIER"} <= owner_titles
        # anonymous + other user: only public-article activity, no private titles
        for hdr in (None, make_headers("u2")):
            body = self._profile(client, hdr)
            titles = {e["summary"] for e in body["recent_activity"]}
            assert "PUBLIC-TITLE" in titles
            assert "SECRET-PRIVATE" not in titles and "SECRET-DOSSIER" not in titles
            blob = json.dumps(body)
            assert "SECRET-PRIVATE" not in blob and "SECRET-DOSSIER" not in blob
