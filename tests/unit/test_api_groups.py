"""HTTP-level tests for group endpoints."""
from __future__ import annotations

import asyncio
import pytest
from tests.conftest import _stable_uuid, make_headers, seed_user


@pytest.mark.asyncio
class TestGroupAPI:
    """Cover /groups endpoints."""

    async def _setup(self, services):
        await seed_user(services["user_repo"], "user-1")

    def test_create_group(self, client, services):
        """POST /groups creates a group."""
        asyncio.get_event_loop().run_until_complete(self._setup(services))
        resp = client.post(
            "/groups",
            json={"name": "Team Alpha", "description": "Test group"},
            headers=make_headers("user-1"),
        )
        assert resp.status_code == 201
        assert resp.json()["name"] == "Team Alpha"

    def test_get_group(self, client, services):
        """GET /groups/:id returns group with members."""
        asyncio.get_event_loop().run_until_complete(self._setup(services))
        h = make_headers("user-1")
        g = client.post("/groups", json={"name": "G1"}, headers=h).json()
        resp = client.get(f"/groups/{g['id']}", headers=h)
        assert resp.status_code == 200
        assert resp.json()["name"] == "G1"

    def test_add_member(self, client, services):
        """POST /groups/:id/members adds a member."""
        asyncio.get_event_loop().run_until_complete(self._setup(services))
        h = make_headers("user-1")
        g = client.post("/groups", json={"name": "G1"}, headers=h).json()
        resp = client.post(
            f"/groups/{g['id']}/members",
            json={"user_id": _stable_uuid("user-1")},
            headers=h,
        )
        assert resp.status_code in (200, 201)

    def test_remove_member(self, client, services):
        """DELETE /groups/:id/members/:uid removes a member."""
        asyncio.get_event_loop().run_until_complete(self._setup(services))
        h = make_headers("user-1")
        g = client.post("/groups", json={"name": "G1"}, headers=h).json()
        client.post(f"/groups/{g['id']}/members", json={"user_id": _stable_uuid("user-1")}, headers=h)
        resp = client.delete(f"/groups/{g["id"]}/members/{_stable_uuid("user-1")}", headers=h)
        assert resp.status_code == 204
