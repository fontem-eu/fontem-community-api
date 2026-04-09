"""HTTP-level tests for user and health endpoints."""
from __future__ import annotations

from tests.conftest import make_headers, _stable_uuid


class TestUserAPI:
    """Cover /users endpoints."""

    def test_get_current_user(self, client):
        """GET /users/me returns the authenticated user (auto-created)."""
        resp = client.get("/users/me", headers=make_headers("user-1", name="Alice"))
        assert resp.status_code == 200
        assert resp.json()["name"] == "Alice"

    def test_get_user_by_id(self, client):
        """GET /users/:id returns public user info."""
        # Auto-create user first
        client.get("/users/me", headers=make_headers("user-1", name="Bob"))
        resp = client.get(f"/users/{_stable_uuid('user-1')}", headers=make_headers("user-1"))
        assert resp.status_code == 200


class TestHealth:
    """Cover health endpoint."""

    def test_health(self, client):
        """GET /health returns ok."""
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
