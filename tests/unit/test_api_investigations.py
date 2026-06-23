"""HTTP-level tests for /investigations endpoints (M2)."""
from __future__ import annotations

import asyncio

import pytest

from tests.conftest import _stable_uuid, make_headers, seed_user


@pytest.mark.asyncio
class TestInvestigationsAPI:
    async def _setup(self, services, *names):
        for n in names:
            await seed_user(services["user_repo"], n)

    def test_create_list_get(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._setup(services, "user-1"))
        h = make_headers("user-1")
        c = client.post("/investigations", json={"name": "Panama", "description": "d"}, headers=h)
        assert c.status_code == 201
        inv = c.json()
        assert inv["name"] == "Panama"
        lst = client.get("/investigations", headers=h).json()
        assert len(lst) == 1
        assert lst[0]["membership"]["is_owner"] is True
        got = client.get(f"/investigations/{inv['id']}", headers=h)
        assert got.status_code == 200
        assert got.json()["membership"]["can_write_stories"] is True

    def test_nonmember_get_forbidden(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._setup(services, "user-1", "user-2"))
        inv = client.post("/investigations", json={"name": "X"}, headers=make_headers("user-1")).json()
        resp = client.get(f"/investigations/{inv['id']}", headers=make_headers("user-2"))
        assert resp.status_code == 403

    def test_member_add_list_remove(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._setup(services, "user-1", "user-2"))
        h = make_headers("user-1")
        inv = client.post("/investigations", json={"name": "X"}, headers=h).json()
        u2 = _stable_uuid("user-2")
        add = client.post(
            f"/investigations/{inv['id']}/members",
            json={"user_id": u2, "can_write_stories": True},
            headers=h,
        )
        assert add.status_code == 201
        members = client.get(f"/investigations/{inv['id']}/members", headers=h).json()
        assert len(members) == 2
        rm = client.delete(f"/investigations/{inv['id']}/members/{u2}", headers=h)
        assert rm.status_code == 204
        assert len(client.get(f"/investigations/{inv['id']}/members", headers=h).json()) == 1

    def test_owner_invariant_conflict(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._setup(services, "user-1", "user-2"))
        h = make_headers("user-1")
        inv = client.post("/investigations", json={"name": "X"}, headers=h).json()
        u2 = _stable_uuid("user-2")
        # make u2 an owner
        client.post(
            f"/investigations/{inv['id']}/members",
            json={"user_id": u2, "is_owner": True}, headers=h,
        )
        # u1 cannot change another owner -> 409
        resp = client.put(
            f"/investigations/{inv['id']}/members/{u2}",
            json={"can_write_stories": True}, headers=h,
        )
        assert resp.status_code == 409

    def test_update_and_delete(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._setup(services, "user-1"))
        h = make_headers("user-1")
        inv = client.post("/investigations", json={"name": "X"}, headers=h).json()
        upd = client.put(f"/investigations/{inv['id']}", json={"name": "Y"}, headers=h)
        assert upd.status_code == 200 and upd.json()["name"] == "Y"
        d = client.delete(f"/investigations/{inv['id']}?content=orphan", headers=h)
        assert d.status_code == 204
        assert client.get(f"/investigations/{inv['id']}", headers=h).status_code == 404

    def test_unknown_is_404(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._setup(services, "user-1"))
        resp = client.get(
            "/investigations/00000000-0000-0000-0000-000000000000",
            headers=make_headers("user-1"),
        )
        assert resp.status_code == 404
