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
        # user-2 cannot see it in their "mine" list; reads/writes are denied (403,
        # the uniform shareable-resource idiom — matches visualizations/dossiers).
        assert all(p["id"] != pid for p in client.get("/studio/projects", headers=h2).json())
        assert client.get(f"/studio/projects/{pid}", headers=h2).status_code == 403
        assert client.put(f"/studio/projects/{pid}", json={"name": "x"}, headers=h2).status_code == 403
        assert client.delete(f"/studio/projects/{pid}", headers=h2).status_code == 403

    def test_requires_auth(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._s(services, "user-1"))
        assert client.get("/studio/projects").status_code in (401, 403)
        assert client.post("/studio/projects", json={"name": "x"}).status_code in (401, 403)

    # ── investigation sharing over HTTP (403 propagation + the new routes) ──
    def test_attach_and_investigation_list(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._s(services, "user-1"))
        h = make_headers("user-1")
        pid = client.post("/studio/projects", json={"name": "P"}, headers=h).json()["id"]
        iid = client.post("/investigations", json={"name": "Inv"}, headers=h).json()["id"]
        assert client.post(f"/studio/projects/{pid}/attach",
                           json={"investigation_id": iid}, headers=h).status_code == 201
        listed = client.get(f"/studio/projects?investigation_id={iid}", headers=h).json()
        assert [p["id"] for p in listed] == [pid]
        # detach → gone from the investigation list
        assert client.post(f"/studio/projects/{pid}/detach", headers=h).status_code == 201
        assert client.get(f"/studio/projects?investigation_id={iid}", headers=h).json() == []

    def test_create_directly_on_investigation(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._s(services, "user-1"))
        h = make_headers("user-1")
        iid = client.post("/investigations", json={"name": "Inv"}, headers=h).json()["id"]
        r = client.post("/studio/projects",
                        json={"name": "P", "investigation_id": iid}, headers=h)
        assert r.status_code == 201
        assert r.json()["investigation_id"] == iid

    def test_investigation_role_inheritance_over_http(self, client, services):
        asyncio.get_event_loop().run_until_complete(
            self._s(services, "owner", "viewer", "contrib", "stranger"))
        oh = make_headers("owner")
        iid = client.post("/investigations", json={"name": "Inv"}, headers=oh).json()["id"]
        pid = client.post("/studio/projects",
                          json={"name": "P", "investigation_id": iid}, headers=oh).json()["id"]
        # add a viewer + a contributor (resolved by email server-side)
        client.post(f"/investigations/{iid}/members",
                    json={"email": "viewer@test.com", "role": "viewer"}, headers=oh)
        client.post(f"/investigations/{iid}/members",
                    json={"email": "contrib@test.com", "role": "contributor"}, headers=oh)
        vh, ch, sh = (make_headers("viewer"), make_headers("contrib"), make_headers("stranger"))
        # viewer: read yes, write no
        assert client.get(f"/studio/projects/{pid}", headers=vh).status_code == 200
        assert client.put(f"/studio/projects/{pid}", json={"name": "x"}, headers=vh).status_code == 403
        # contributor: write yes, delete (owner-level) no
        assert client.put(f"/studio/projects/{pid}", json={"name": "ok"}, headers=ch).status_code == 200
        assert client.delete(f"/studio/projects/{pid}", headers=ch).status_code == 403
        # stranger: nothing
        assert client.get(f"/studio/projects/{pid}", headers=sh).status_code == 403

    def test_direct_share_and_escalation_over_http(self, client, services):
        asyncio.get_event_loop().run_until_complete(
            self._s(services, "owner", "grantee", "outsider"))
        oh, gh = make_headers("owner"), make_headers("grantee")
        pid = client.post("/studio/projects", json={"name": "P"}, headers=oh).json()["id"]
        # grantee has no access yet
        assert client.get(f"/studio/projects/{pid}", headers=gh).status_code == 403
        # owner grants editor
        assert client.post(f"/studio/projects/{pid}/access",
                           json={"email": "grantee@test.com", "level": "editor"},
                           headers=oh).status_code == 201
        assert client.get(f"/studio/projects/{pid}", headers=gh).status_code == 200
        assert client.put(f"/studio/projects/{pid}", json={"name": "edited"}, headers=gh).status_code == 200
        # effective-access lists owner + the direct grant
        eff = client.get(f"/studio/projects/{pid}/effective-access", headers=oh).json()
        sources = {r["level"] for r in eff}
        assert "editor" in sources
        # PRIVILEGE ESCALATION: an editor grant must NOT let the grantee re-share
        gid = client.post("/studio/projects", json={"name": "tmp"}, headers=gh).json()["id"]  # noqa: F841
        assert client.post(f"/studio/projects/{pid}/access",
                           json={"email": "outsider@test.com", "level": "editor"},
                           headers=gh).status_code == 403
        # owner revokes → grantee locked out again
        # (find grantee id from effective-access)
        gid_real = next(r["user_id"] for r in eff if r["level"] == "editor" and r["source"] == "direct")
        assert client.delete(f"/studio/projects/{pid}/access/{gid_real}", headers=oh).status_code == 204
        assert client.get(f"/studio/projects/{pid}", headers=gh).status_code == 403

    def test_my_access_block_in_responses(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._s(services, "owner", "viewer"))
        oh = make_headers("owner")
        iid = client.post("/investigations", json={"name": "Inv"}, headers=oh).json()["id"]
        pid = client.post("/studio/projects",
                          json={"name": "P", "investigation_id": iid}, headers=oh).json()["id"]
        # owner's own create response is owner-tier
        assert client.get(f"/studio/projects/{pid}", headers=oh).json()["my_access"]["level"] == "owner"
        client.post(f"/investigations/{iid}/members",
                    json={"email": "viewer@test.com", "role": "viewer"}, headers=oh)
        vh = make_headers("viewer")
        acc = client.get(f"/studio/projects/{pid}", headers=vh).json()["my_access"]
        assert acc["level"] == "viewer" and acc["can_edit"] is False and acc["can_share"] is False
        # investigation listing carries per-project access for the viewer too
        listed = client.get(f"/studio/projects?investigation_id={iid}", headers=vh).json()
        assert listed[0]["my_access"]["level"] == "viewer"
