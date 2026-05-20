"""HTTP-level tests for issue endpoints (covers routers/issues.py)."""
from __future__ import annotations

import asyncio

import pytest
from tests.conftest import make_headers, seed_user


@pytest.mark.asyncio
class TestIssueAPI:
    """Cover issue CRUD via the HTTP API."""

    async def _setup(self, services):
        await seed_user(services["user_repo"], "user-1", trust_level="contributor")

    def test_create_issue(self, client, services):
        """POST /issues creates an issue."""
        asyncio.get_event_loop().run_until_complete(self._setup(services))
        resp = client.post(
            "/issues",
            json={
                "title": "Bad data",
                "issue_type": "incorrect_data",
                "entity_type": "company",
                "entity_id": "gmr-123",
                "body": "Revenue looks wrong",
            },
            headers=make_headers("user-1"),
        )
        assert resp.status_code == 201
        assert resp.json()["title"] == "Bad data"

    def test_list_issues(self, client, services):
        """GET /issues returns issues."""
        asyncio.get_event_loop().run_until_complete(self._setup(services))
        h = make_headers("user-1")
        client.post(
            "/issues",
            json={"title": "I1", "issue_type": "other", "entity_type": "company", "entity_id": "x", "body": "b"},
            headers=h,
        )
        resp = client.get("/issues", headers=h)
        assert resp.status_code == 200

    def test_get_issue(self, client, services):
        """GET /issues/:id returns issue detail."""
        asyncio.get_event_loop().run_until_complete(self._setup(services))
        h = make_headers("user-1")
        create = client.post(
            "/issues",
            json={"title": "I1", "issue_type": "other", "entity_type": "company", "entity_id": "x", "body": "b"},
            headers=h,
        )
        iid = create.json()["id"]
        resp = client.get(f"/issues/{iid}", headers=h)
        assert resp.status_code == 200
        assert resp.json()["title"] == "I1"

    def test_add_comment(self, client, services):
        """POST /issues/:id/comments adds a comment."""
        asyncio.get_event_loop().run_until_complete(self._setup(services))
        h = make_headers("user-1")
        create = client.post(
            "/issues",
            json={"title": "I1", "issue_type": "other", "entity_type": "company", "entity_id": "x", "body": "b"},
            headers=h,
        )
        iid = create.json()["id"]
        resp = client.post(f"/issues/{iid}/comments", json={"body": "A comment"}, headers=h)
        assert resp.status_code == 201

    def test_vote_on_issue(self, client, services):
        """POST /issues/:id/vote records a vote."""
        asyncio.get_event_loop().run_until_complete(self._setup(services))
        h = make_headers("user-1")
        create = client.post(
            "/issues",
            json={"title": "I1", "issue_type": "other", "entity_type": "company", "entity_id": "x", "body": "b"},
            headers=h,
        )
        iid = create.json()["id"]
        resp = client.post(f"/issues/{iid}/vote", json={"direction": "up"}, headers=h)
        assert resp.status_code == 200
