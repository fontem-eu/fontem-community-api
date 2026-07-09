"""Profile API: anonymous GET /users/{id}/profile + authed PUT /users/me/profile."""
from __future__ import annotations

import asyncio

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
                            {"name": "Bad", "url": "ftp://nope.io"}]},
            headers=h,
        )
        assert put.status_code == 200, put.text
        assert put.json()["summary"] == "Follows the money."
        # bad-scheme link dropped by validation
        assert put.json()["links"] == [{"name": "Site", "url": "https://s.io"}]

        # anonymous (no headers) can read the public author profile
        resp = client.get(f"/users/{_stable_uuid('u1')}/profile")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["summary"] == "Follows the money."
        assert body["links"] == [{"name": "Site", "url": "https://s.io"}]
        assert any(a["title"] == "Money trail" for a in body["articles"])

    def test_get_profile_unknown_user_404(self, client):
        resp = client.get(f"/users/{_stable_uuid('ghost')}/profile")
        assert resp.status_code == 404, resp.text
