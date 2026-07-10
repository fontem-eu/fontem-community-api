"""Profile API: anonymous GET /users/{id}/profile, authed PUT /users/me/profile
(link normalisation), and avatar upload."""
from __future__ import annotations

import asyncio
from io import BytesIO

from PIL import Image

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
