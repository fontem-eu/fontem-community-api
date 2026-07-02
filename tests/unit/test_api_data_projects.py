"""HTTP tests for /studio — data projects, queries and plots (owner-private)."""
from __future__ import annotations

import asyncio

import pytest

from tests.conftest import make_headers, seed_user


@pytest.mark.asyncio
class TestDataProjectsAPI:
    async def _s(self, services, *names):
        for n in names:
            await seed_user(services["user_repo"], n)

    def test_project_crud(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._s(services, "user-1"))
        h = make_headers("user-1")
        r = client.post("/studio/projects", json={"name": "Corruption"}, headers=h)
        assert r.status_code == 201
        pid = r.json()["id"]
        assert r.json()["name"] == "Corruption"
        assert r.json()["created_by"]
        assert r.json()["queries"] == [] and r.json()["plots"] == []
        # list
        assert any(p["id"] == pid for p in client.get("/studio/projects", headers=h).json())
        # rename
        assert client.put(f"/studio/projects/{pid}", json={"name": "Renamed"}, headers=h).json()["name"] == "Renamed"
        # delete → gone
        assert client.delete(f"/studio/projects/{pid}", headers=h).status_code == 204
        assert client.get(f"/studio/projects/{pid}", headers=h).status_code == 404

    def test_query_lifecycle_nested_in_project(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._s(services, "user-1"))
        h = make_headers("user-1")
        pid = client.post("/studio/projects", json={"name": "P"}, headers=h).json()["id"]
        # add
        q = client.post(f"/studio/projects/{pid}/queries",
                        json={"name": "contracts", "lang": "cypher", "query": "MATCH (n) RETURN n"}, headers=h)
        assert q.status_code == 201
        qid = q.json()["id"]
        assert q.json()["lang"] == "cypher"
        # nested in project GET
        got = client.get(f"/studio/projects/{pid}", headers=h).json()
        assert [x["id"] for x in got["queries"]] == [qid]
        # update
        upd = client.put(f"/studio/projects/{pid}/queries/{qid}",
                         json={"name": "awards", "query": "MATCH (c) RETURN c"}, headers=h)
        assert upd.json()["name"] == "awards"
        assert upd.json()["query"] == "MATCH (c) RETURN c"
        # duplicate
        dup = client.post(f"/studio/projects/{pid}/queries/{qid}/duplicate", headers=h)
        assert dup.status_code == 201
        assert dup.json()["name"] == "awards copy"
        assert len(client.get(f"/studio/projects/{pid}", headers=h).json()["queries"]) == 2
        # delete
        assert client.delete(f"/studio/projects/{pid}/queries/{qid}", headers=h).status_code == 204
        assert len(client.get(f"/studio/projects/{pid}", headers=h).json()["queries"]) == 1

    def test_plot_lifecycle_roundtrips_spec(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._s(services, "user-1"))
        h = make_headers("user-1")
        pid = client.post("/studio/projects", json={"name": "P"}, headers=h).json()["id"]
        spec = {"sources": [{"name": "q1", "lang": "cypher", "query": "MATCH (n) RETURN n"}],
                "transform": "SELECT * FROM q1", "chart": "bar_h", "x": "a", "y": "b"}
        pl = client.post(f"/studio/projects/{pid}/plots", json={"name": "Overview", "spec": spec}, headers=h)
        assert pl.status_code == 201
        plid = pl.json()["id"]
        assert pl.json()["spec"] == spec
        # nested + update
        assert [x["id"] for x in client.get(f"/studio/projects/{pid}", headers=h).json()["plots"]] == [plid]
        upd = client.put(f"/studio/projects/{pid}/plots/{plid}",
                         json={"name": "Renamed", "spec": {"chart": "stat"}}, headers=h)
        assert upd.json()["name"] == "Renamed"
        assert upd.json()["spec"] == {"chart": "stat"}
        assert client.delete(f"/studio/projects/{pid}/plots/{plid}", headers=h).status_code == 204

    def test_ownership_is_private(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._s(services, "user-1", "user-2"))
        pid = client.post("/studio/projects", json={"name": "Secret"},
                          headers=make_headers("user-1")).json()["id"]
        h2 = make_headers("user-2")
        # user-2 cannot see it in their list, read it, rename it, or delete it
        assert all(p["id"] != pid for p in client.get("/studio/projects", headers=h2).json())
        assert client.get(f"/studio/projects/{pid}", headers=h2).status_code == 404
        assert client.put(f"/studio/projects/{pid}", json={"name": "x"}, headers=h2).status_code == 404
        assert client.delete(f"/studio/projects/{pid}", headers=h2).status_code == 404

    def test_requires_auth(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._s(services, "user-1"))
        assert client.get("/studio/projects").status_code in (401, 403)
        assert client.post("/studio/projects", json={"name": "x"}).status_code in (401, 403)
