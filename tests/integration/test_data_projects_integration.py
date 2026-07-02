"""Integration tests for Data Studio — /studio against real PostgreSQL.

Validates the PG repo's selectin nested loading (queries + plots under a
project), spec JSONB round-trip, ownership, and persistence across requests.
"""
from __future__ import annotations

from tests.integration.conftest import make_headers


class TestDataProjectsIntegration:
    def test_project_with_nested_queries_and_plots(self, client, user_id):
        h = make_headers(user_id)
        pid = client.post("/studio/projects", json={"name": "Corruption"}, headers=h).json()["id"]
        assert len(pid) == 36
        # add a query + a plot
        q = client.post(f"/studio/projects/{pid}/queries",
                        json={"name": "contracts", "lang": "cypher", "query": "MATCH (n) RETURN n"}, headers=h)
        assert q.status_code == 201
        spec = {"sources": [{"name": "q1", "lang": "cypher", "query": "MATCH (n) RETURN n"}],
                "transform": "SELECT * FROM q1", "chart": "bar_h", "x": "a", "y": "b"}
        pl = client.post(f"/studio/projects/{pid}/plots", json={"name": "Overview", "spec": spec}, headers=h)
        assert pl.status_code == 201
        # GET reloads with selectin — nested queries + plots present, spec intact
        got = client.get(f"/studio/projects/{pid}", headers=h).json()
        assert [x["name"] for x in got["queries"]] == ["contracts"]
        assert got["plots"][0]["spec"] == spec
        assert got["created_by"] == user_id

    def test_persistence_and_list(self, client, user_id):
        h = make_headers(user_id)
        client.post("/studio/projects", json={"name": "P1"}, headers=h)
        client.post("/studio/projects", json={"name": "P2"}, headers=h)
        names = [p["name"] for p in client.get("/studio/projects", headers=h).json()]
        assert "P1" in names and "P2" in names

    def test_delete_cascades_and_ownership(self, client, user_id, user2_id):
        h, h2 = make_headers(user_id), make_headers(user2_id)
        pid = client.post("/studio/projects", json={"name": "Secret"}, headers=h).json()["id"]
        qid = client.post(f"/studio/projects/{pid}/queries", json={"query": "MATCH (n) RETURN n"}, headers=h).json()["id"]
        # other user can't touch it
        assert client.get(f"/studio/projects/{pid}", headers=h2).status_code == 404
        assert client.delete(f"/studio/projects/{pid}/queries/{qid}", headers=h2).status_code == 404
        # delete project cascades (query gone with it)
        assert client.delete(f"/studio/projects/{pid}", headers=h).status_code == 204
        assert client.get(f"/studio/projects/{pid}", headers=h).status_code == 404
