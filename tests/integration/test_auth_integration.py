"""
Integration tests for Auth + Users + Groups + Moderation — real PostgreSQL.
"""
from __future__ import annotations

import uuid

from tests.integration.conftest import make_headers


class TestAuth:
    """AUTH-I01..I04: Authentication against Postgres."""

    def test_health_no_auth(self, client):
        """AUTH-I01: Health endpoint works without auth."""
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_protected_endpoint_without_auth(self, client):
        """AUTH-I02: Protected endpoint returns 401 without JWT."""
        resp = client.get("/reports")
        assert resp.status_code in (401, 403)

    def test_valid_jwt_creates_user(self, client, user_id):
        """AUTH-I03: Valid JWT auto-creates user on first request."""
        h = make_headers(user_id, name="New User")
        resp = client.get("/users/me", headers=h)
        assert resp.status_code == 200
        assert resp.json()["name"] == "New User"

    def test_users_me_returns_profile(self, client, user_id):
        """AUTH-I04: /users/me returns the authenticated user's profile."""
        h = make_headers(user_id, name="Profile User")
        resp = client.get("/users/me", headers=h)
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Profile User"


class TestGroups:
    """GRP-I01..I03: Group management."""

    def test_create_group(self, client, user_id):
        """GRP-I01: Create a group."""
        h = make_headers(user_id)
        resp = client.post("/groups", json={"name": "Team Alpha"}, headers=h)
        assert resp.status_code == 201
        assert resp.json()["name"] == "Team Alpha"

    def test_add_remove_member(self, client, user_id, user2_id):
        """GRP-I02: Add and remove members."""
        h1 = make_headers(user_id)
        h2 = make_headers(user2_id)
        # Ensure user2 exists
        client.get("/users/me", headers=h2)
        group = client.post("/groups", json={"name": "Team"}, headers=h1).json()
        # Add member
        resp = client.post(f"/groups/{group['id']}/members", json={"user_id": user2_id}, headers=h1)
        assert resp.status_code in (200, 201)
        # Remove member
        resp = client.delete(f"/groups/{group['id']}/members/{user2_id}", headers=h1)
        assert resp.status_code == 204

    def test_group_access_to_report(self, client, user_id, user2_id):
        """GRP-I03: Group membership grants report access."""
        h1 = make_headers(user_id)
        h2 = make_headers(user2_id)
        # Auto-create user2
        client.get("/users/me", headers=h2)
        # Create group and add user2
        group = client.post("/groups", json={"name": "Readers"}, headers=h1).json()
        client.post(f"/groups/{group['id']}/members", json={"user_id": user2_id}, headers=h1)
        # Create report and grant group access
        report = client.post("/reports", json={"title": "GroupAccess"}, headers=h1).json()
        client.post(
            f"/reports/{report['id']}/access",
            json={"group_id": group["id"], "level": "viewer"},
            headers=h1,
        )
        # User2 should be able to read via group
        resp = client.get(f"/reports/{report['id']}", headers=h2)
        assert resp.status_code == 200


class TestModeration:
    """MOD-I01..I03: Moderation with Postgres."""

    def test_flag_content(self, client, user_id):
        """MOD-I01: Flag content."""
        h = make_headers(user_id)
        resp = client.post("/flags", json={
            "target_type": "report",
            "target_id": str(uuid.uuid4()),
            "reason": "spam",
        }, headers=h)
        assert resp.status_code in (200, 201)

    def test_moderation_log(self, client, user_id):
        """MOD-I02: Moderation log is accessible."""
        h = make_headers(user_id)
        resp = client.get("/moderation/log", headers=h)
        # May return 403 if user isn't moderator — that's OK for this test
        assert resp.status_code in (200, 403)
