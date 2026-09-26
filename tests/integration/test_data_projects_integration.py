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
        # other user can't touch it (403 — resource exists but access denied)
        assert client.get(f"/studio/projects/{pid}", headers=h2).status_code == 403
        assert client.delete(f"/studio/projects/{pid}/queries/{qid}", headers=h2).status_code == 403
        # delete project cascades (query gone with it)
        assert client.delete(f"/studio/projects/{pid}", headers=h).status_code == 204
        assert client.get(f"/studio/projects/{pid}", headers=h).status_code == 404

    def test_direct_share_grants_and_revokes(self, client, user_id, user2_id):
        h, h2 = make_headers(user_id), make_headers(user2_id)
        pid = client.post("/studio/projects", json={"name": "Shared"}, headers=h).json()["id"]
        assert client.get(f"/studio/projects/{pid}", headers=h2).status_code == 403
        # owner grants editor to user2
        assert client.post(f"/studio/projects/{pid}/access",
                           json={"user_id": user2_id, "level": "editor"},
                           headers=h).status_code == 201
        assert client.get(f"/studio/projects/{pid}", headers=h2).status_code == 200
        assert client.put(f"/studio/projects/{pid}", json={"name": "edited"},
                          headers=h2).status_code == 200
        # editor grant must NOT confer re-share (privilege escalation guard)
        assert client.post(f"/studio/projects/{pid}/access",
                           json={"user_id": user_id, "level": "editor"},
                           headers=h2).status_code == 403
        # revoke → locked out again
        assert client.delete(f"/studio/projects/{pid}/access/{user2_id}",
                             headers=h).status_code == 204
        assert client.get(f"/studio/projects/{pid}", headers=h2).status_code == 403

    def test_attach_to_investigation_persists(self, client, user_id):
        h = make_headers(user_id)
        pid = client.post("/studio/projects", json={"name": "P"}, headers=h).json()["id"]
        iid = client.post("/investigations", json={"name": "Inv"}, headers=h).json()["id"]
        assert client.post(f"/studio/projects/{pid}/attach",
                           json={"investigation_id": iid}, headers=h).status_code == 201
        # reloaded from PG: investigation_id column round-trips
        assert client.get(f"/studio/projects/{pid}", headers=h).json()["investigation_id"] == iid
        assert [p["id"] for p in
                client.get(f"/studio/projects?investigation_id={iid}", headers=h).json()] == [pid]


class TestDataProjectsPagingOnPostgres:
    """The keyset cursor against a real timestamptz column.

    Unit tests page the in-memory repository, which compares Python tuples.
    What only Postgres can show is whether the ``updated_at`` the API
    serialises parses back into a value that ``(updated_at, id) < (...)``
    compares correctly — a lost microsecond or a dropped offset would repeat
    or skip a row at every page boundary.
    """

    def test_cursor_walks_every_project_exactly_once(self, client, user_id):
        h = make_headers(user_id)
        made = {client.post("/studio/projects", json={"name": f"page-{i}"}, headers=h).json()["id"]
                for i in range(7)}
        seen, before = [], ""
        while True:
            params = {"limit": 3, **({"before": before} if before else {})}
            page = client.get("/studio/projects", params=params, headers=h).json()
            seen += [p["id"] for p in page]
            if len(page) < 3:
                break
            before = f'{page[-1]["updated_at"]}|{page[-1]["id"]}'
        mine = [pid for pid in seen if pid in made]
        assert len(mine) == len(set(mine)) == 7

    def test_ties_on_updated_at_page_by_id(self, client, user_id, _postgres):
        """Same instant for all five, set in the database itself, so the only
        thing keeping pages apart is the id tie-break in the SQL."""
        import sqlalchemy as sa  # pylint: disable=import-outside-toplevel

        h = make_headers(user_id)
        ids = [client.post("/studio/projects", json={"name": f"tie-{i}"}, headers=h).json()["id"]
               for i in range(5)]
        url = _postgres.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")
        engine = sa.create_engine(url)
        with engine.begin() as conn:
            conn.execute(
                sa.text("UPDATE data_projects SET updated_at = '2030-01-01T00:00:00+00' "
                        "WHERE id = ANY(CAST(:ids AS uuid[]))"),
                {"ids": ids},
            )
        engine.dispose()
        seen, before = [], ""
        for _ in range(10):
            params = {"limit": 2, **({"before": before} if before else {})}
            page = client.get("/studio/projects", params=params, headers=h).json()
            seen += [p["id"] for p in page if p["id"] in ids]
            if len(page) < 2 or set(ids) <= set(seen):
                break
            before = f'{page[-1]["updated_at"]}|{page[-1]["id"]}'
        assert sorted(seen) == sorted(ids)
