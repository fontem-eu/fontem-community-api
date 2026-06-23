"""HTTP tests for investigation story association + delete modes (M4)."""
from __future__ import annotations

import asyncio

import pytest

from tests.conftest import _stable_uuid, make_headers, seed_user


@pytest.mark.asyncio
class TestInvestigationStoriesAPI:
    async def _s(self, services, *names):
        for n in names:
            await seed_user(services["user_repo"], n)

    def _new_inv_and_story(self, client, h):
        iid = client.post("/investigations", json={"name": "Inv"}, headers=h).json()["id"]
        sid = client.post(
            "/data-stories", json={"title": "A", "abstract": ""}, headers=h).json()["id"]
        return iid, sid

    def test_add_list_remove_story(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._s(services, "user-1"))
        h = make_headers("user-1")
        iid, sid = self._new_inv_and_story(client, h)
        assert client.post(
            f"/investigations/{iid}/stories", json={"report_id": sid}, headers=h,
        ).status_code == 201
        assert [s["id"] for s in client.get(f"/investigations/{iid}/stories", headers=h).json()] == [sid]
        assert client.delete(f"/investigations/{iid}/stories/{sid}", headers=h).status_code == 204
        assert client.get(f"/investigations/{iid}/stories", headers=h).json() == []

    def test_viewer_cannot_add_story(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._s(services, "user-1", "user-2"))
        h1 = make_headers("user-1")
        iid, _ = self._new_inv_and_story(client, h1)
        u2_email = f"{_stable_uuid('user-2')}@test.com"
        client.post(f"/investigations/{iid}/members", json={"email": u2_email}, headers=h1)
        h2 = make_headers("user-2")
        sid = client.post("/data-stories", json={"title": "B", "abstract": ""}, headers=h2).json()["id"]
        assert client.post(
            f"/investigations/{iid}/stories", json={"report_id": sid}, headers=h2,
        ).status_code == 403

    def test_delete_orphan_keeps_story(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._s(services, "user-1"))
        h = make_headers("user-1")
        iid, sid = self._new_inv_and_story(client, h)
        client.post(f"/investigations/{iid}/stories", json={"report_id": sid}, headers=h)
        assert client.delete(f"/investigations/{iid}?content=orphan", headers=h).status_code == 204
        assert client.get(f"/data-stories/{sid}", headers=h).status_code == 200  # survives

    def test_delete_cascade_removes_story(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._s(services, "user-1"))
        h = make_headers("user-1")
        iid, sid = self._new_inv_and_story(client, h)
        client.post(f"/investigations/{iid}/stories", json={"report_id": sid}, headers=h)
        assert client.delete(f"/investigations/{iid}?content=cascade", headers=h).status_code == 204
        assert client.get(f"/data-stories/{sid}", headers=h).status_code == 404  # gone
