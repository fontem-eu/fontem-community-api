"""Activity feed API — CUD across all four entity types is recorded."""
from __future__ import annotations

import asyncio

import pytest

from tests.conftest import make_headers, seed_user


@pytest.mark.asyncio
class TestActivityAPI:
    async def _setup(self, services):
        await seed_user(services["user_repo"], "u1")

    def test_cud_across_entities_is_recorded(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._setup(services))
        h = make_headers("u1")
        sid = client.post("/data-stories", json={"title": "S1", "abstract": ""}, headers=h).json()["id"]
        iid = client.post("/investigations", json={"name": "Inv1"}, headers=h).json()["id"]
        client.post("/dossiers", json={"name": "Dos1", "investigation_id": iid}, headers=h)
        client.post("/issues", json={"title": "Iss1", "body": "x", "issue_type": "other"}, headers=h)
        # update + delete a story too
        client.put(f"/data-stories/{sid}", json={"title": "S1-edited"}, headers=h)
        client.delete(f"/data-stories/{sid}", headers=h)

        resp = client.get("/activity", headers=h)
        assert resp.status_code == 200, resp.text
        events = resp.json()
        pairs = [(e["entity_type"], e["action"]) for e in events]
        assert ("story", "created") in pairs
        assert ("investigation", "created") in pairs
        assert ("dossier", "created") in pairs
        assert ("issue", "created") in pairs
        assert ("story", "updated") in pairs
        assert ("story", "deleted") in pairs
        # newest-first: the delete is the most recent event
        assert events[0]["entity_type"] == "story" and events[0]["action"] == "deleted"
        # deleted event still carries the title captured at delete time
        assert events[0]["summary"] == "S1-edited"

    def test_activity_is_per_user(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._setup(services))
        asyncio.get_event_loop().run_until_complete(seed_user(services["user_repo"], "u2"))
        client.post("/investigations", json={"name": "MineU1"}, headers=make_headers("u1"))
        resp = client.get("/activity", headers=make_headers("u2"))
        assert resp.status_code == 200
        assert resp.json() == []  # u2 sees none of u1's activity
