"""HTTP tests for /visualizations (M5)."""
from __future__ import annotations

import asyncio

import pytest

from tests.conftest import make_headers, seed_user


@pytest.mark.asyncio
class TestVisualizationsAPI:
    async def _s(self, services, *names):
        for n in names:
            await seed_user(services["user_repo"], n)

    def test_crud_and_attach(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._s(services, "user-1"))
        h = make_headers("user-1")
        v = client.post("/visualizations", json={
            "name": "Snap", "widget_type": "chart_snapshot", "config": {"entityId": "AAPL"},
        }, headers=h)
        assert v.status_code == 201
        vid = v.json()["id"]
        assert v.json()["config"] == {"entityId": "AAPL"}
        assert any(x["id"] == vid for x in client.get("/visualizations", headers=h).json())
        # attach to an investigation the user owns
        iid = client.post("/investigations", json={"name": "Inv"}, headers=h).json()["id"]
        assert client.post(f"/visualizations/{vid}/attach", json={"investigation_id": iid}, headers=h).status_code == 201
        listed = client.get(f"/visualizations?investigation_id={iid}", headers=h).json()
        assert [x["id"] for x in listed] == [vid]
        assert client.delete(f"/visualizations/{vid}", headers=h).status_code == 204

    def test_create_directly_on_investigation(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._s(services, "user-1"))
        h = make_headers("user-1")
        iid = client.post("/investigations", json={"name": "Inv"}, headers=h).json()["id"]
        v = client.post("/visualizations", json={
            "widget_type": "map", "config": {}, "investigation_id": iid,
        }, headers=h)
        assert v.status_code == 201
        assert [x["id"] for x in client.get(f"/visualizations?investigation_id={iid}", headers=h).json()] == [v.json()["id"]]

    def test_nonowner_cannot_read(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._s(services, "user-1", "user-2"))
        vid = client.post("/visualizations", json={"widget_type": "map", "config": {}},
                          headers=make_headers("user-1")).json()["id"]
        assert client.get(f"/visualizations/{vid}", headers=make_headers("user-2")).status_code == 403
