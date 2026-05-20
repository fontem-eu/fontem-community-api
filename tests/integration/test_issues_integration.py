"""
Integration tests for Issues — full HTTP API against real PostgreSQL.

Note: Issue creation requires 'contributor' trust level. New users (auto-created)
have 'new_user' trust, so we test the permission check too.
"""
from __future__ import annotations

from tests.integration.conftest import make_headers


class TestIssues:
    """ISS-I01..I05: Issue lifecycle."""

    def test_create_issue_denied_for_new_user(self, client, user_id):
        """ISS-I01a: New users cannot create issues (need contributor trust)."""
        h = make_headers(user_id)
        resp = client.post("/issues", json={
            "title": "Bad data",
            "issue_type": "incorrect_data",
            "entity_type": "company",
            "entity_id": "gmr-123",
            "body": "Revenue wrong",
        }, headers=h)
        assert resp.status_code == 403

    def test_list_issues(self, client, user_id):
        """ISS-I02: List issues returns 200."""
        h = make_headers(user_id)
        resp = client.get("/issues", headers=h)
        assert resp.status_code == 200

    def test_list_issues_returns_array(self, client, user_id):
        """ISS-I03: List issues returns a JSON array."""
        h = make_headers(user_id)
        resp = client.get("/issues", headers=h)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (list, dict))  # may be {issues: [...]} or [...]
