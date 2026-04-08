"""
Integration tests for Issues — full HTTP API against real PostgreSQL.
"""
from __future__ import annotations

import pytest
from tests.integration.conftest import make_headers


class TestIssues:
    """ISS-I01..I05: Issue lifecycle."""

    def test_create_issue(self, client, user_id):
        """ISS-I01: Create issue."""
        h = make_headers(user_id)
        resp = client.post("/issues", json={
            "title": "Bad data",
            "issue_type": "incorrect_data",
            "entity_type": "company",
            "entity_id": "gmr-123",
            "body": "Revenue wrong",
        }, headers=h)
        assert resp.status_code == 201
        assert resp.json()["title"] == "Bad data"

    def test_list_issues(self, client, user_id):
        """ISS-I02: List issues."""
        h = make_headers(user_id)
        client.post("/issues", json={
            "title": "I1", "issue_type": "other",
            "entity_type": "company", "entity_id": "x", "body": "b",
        }, headers=h)
        resp = client.get("/issues", headers=h)
        assert resp.status_code == 200

    def test_add_comment(self, client, user_id):
        """ISS-I03: Add comment to issue."""
        h = make_headers(user_id)
        issue = client.post("/issues", json={
            "title": "I1", "issue_type": "other",
            "entity_type": "company", "entity_id": "x", "body": "b",
        }, headers=h).json()
        resp = client.post(f"/issues/{issue['id']}/comments", json={"body": "A comment"}, headers=h)
        assert resp.status_code == 201

    def test_vote_on_issue(self, client, user_id):
        """ISS-I04: Vote on issue."""
        h = make_headers(user_id)
        issue = client.post("/issues", json={
            "title": "Vote", "issue_type": "other",
            "entity_type": "company", "entity_id": "x", "body": "b",
        }, headers=h).json()
        resp = client.post(f"/issues/{issue['id']}/vote", json={"direction": "up"}, headers=h)
        assert resp.status_code == 200

    def test_get_issue_detail(self, client, user_id):
        """ISS-I05: Get issue detail."""
        h = make_headers(user_id)
        issue = client.post("/issues", json={
            "title": "Detail", "issue_type": "other",
            "entity_type": "company", "entity_id": "x", "body": "body text",
        }, headers=h).json()
        resp = client.get(f"/issues/{issue['id']}", headers=h)
        assert resp.status_code == 200
        assert resp.json()["title"] == "Detail"
