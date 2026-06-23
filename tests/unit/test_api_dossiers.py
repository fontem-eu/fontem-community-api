"""HTTP-level tests for /dossiers endpoints (M3)."""
from __future__ import annotations

import asyncio

import pytest

from tests.conftest import make_headers, seed_user


@pytest.mark.asyncio
class TestDossiersAPI:
    async def _s(self, services, *names):
        for n in names:
            await seed_user(services["user_repo"], n)

    def test_crud_tree_and_articles(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._s(services, "user-1"))
        h = make_headers("user-1")
        d = client.post("/dossiers", json={"name": "Files"}, headers=h)
        assert d.status_code == 201
        did = d.json()["id"]
        assert any(x["id"] == did for x in client.get("/dossiers", headers=h).json())
        story = client.post("/data-stories", json={"title": "A", "abstract": ""}, headers=h).json()
        add = client.post(f"/dossiers/{did}/articles", json={"report_id": story["id"]}, headers=h)
        assert add.status_code == 201
        got = client.get(f"/dossiers/{did}", headers=h).json()
        assert len(got["articles"]) == 1 and got["articles"][0]["id"] == story["id"]
        upd = client.put(f"/dossiers/{did}", json={"name": "Renamed"}, headers=h)
        assert upd.status_code == 200 and upd.json()["name"] == "Renamed"
        rm = client.delete(f"/dossiers/{did}/articles/{story['id']}", headers=h)
        assert rm.status_code == 204
        assert client.get(f"/dossiers/{did}", headers=h).json()["articles"] == []
        assert client.delete(f"/dossiers/{did}?content=orphan", headers=h).status_code == 204

    def test_nonowner_forbidden(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._s(services, "user-1", "user-2"))
        d = client.post("/dossiers", json={"name": "F"}, headers=make_headers("user-1")).json()
        assert client.get(f"/dossiers/{d['id']}", headers=make_headers("user-2")).status_code == 403

    def test_unknown_404(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._s(services, "user-1"))
        assert client.get(
            "/dossiers/00000000-0000-0000-0000-000000000000", headers=make_headers("user-1"),
        ).status_code == 404
