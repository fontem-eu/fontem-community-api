"""HTTP-level tests for sharing/access endpoints."""
from __future__ import annotations

import asyncio
import pytest
from tests.conftest import make_headers, seed_user


@pytest.mark.asyncio
class TestSharingAPI:
    """Cover /reports/:id/access endpoints."""

    async def _setup(self, services):
        await seed_user(services["user_repo"], "owner-1")
        await seed_user(services["user_repo"], "viewer-1")

    def test_get_access_list(self, client, services):
        """GET /reports/:id/access returns access grants."""
        asyncio.get_event_loop().run_until_complete(self._setup(services))
        h = make_headers("owner-1")
        r = client.post("/reports", json={"title": "R"}, headers=h).json()
        resp = client.get(f"/reports/{r['id']}/access", headers=h)
        assert resp.status_code == 200

    def test_grant_access(self, client, services):
        """POST /reports/:id/access grants user access."""
        asyncio.get_event_loop().run_until_complete(self._setup(services))
        h = make_headers("owner-1")
        r = client.post("/reports", json={"title": "R"}, headers=h).json()
        resp = client.post(
            f"/reports/{r['id']}/access",
            json={"user_id": "viewer-1", "level": "viewer"},
            headers=h,
        )
        assert resp.status_code in (200, 201)

    def test_viewer_can_read_shared_report(self, client, services):
        """After granting access, viewer can read the report."""
        asyncio.get_event_loop().run_until_complete(self._setup(services))
        ho = make_headers("owner-1")
        r = client.post("/reports", json={"title": "Shared"}, headers=ho).json()
        client.post(
            f"/reports/{r['id']}/access",
            json={"user_id": "viewer-1", "level": "viewer"},
            headers=ho,
        )
        hv = make_headers("viewer-1")
        resp = client.get(f"/reports/{r['id']}", headers=hv)
        assert resp.status_code == 200

    def test_revoke_access(self, client, services):
        """DELETE /reports/:id/access/:aid revokes access."""
        asyncio.get_event_loop().run_until_complete(self._setup(services))
        ho = make_headers("owner-1")
        r = client.post("/reports", json={"title": "R"}, headers=ho).json()
        grant = client.post(
            f"/reports/{r['id']}/access",
            json={"user_id": "viewer-1", "level": "viewer"},
            headers=ho,
        ).json()
        aid = grant.get("id") or grant.get("access_id", "unknown")
        resp = client.delete(f"/reports/{r['id']}/access/{aid}", headers=ho)
        assert resp.status_code == 204
