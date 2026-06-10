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

    def test_get_current_user_does_not_leak_credentials(self, client):
        """GET /users/me must NOT expose the bcrypt password_hash
        (login-secret material) or the account-lockout columns
        (failed_login_attempts / locked_until — PII that lets an
        attacker probe whether a credential-stuffing run is making
        progress against a target). 2026-06-10 Schemathesis pass
        caught the leak (the route was doing ``asdict(user)`` and
        dumping every domain field), this test locks the fix in."""
        resp = client.get("/users/me", headers=make_headers("user-1", name="Alice"))
        assert resp.status_code == 200
        body = resp.json()

        # Allowed self-view fields
        assert set(body.keys()) <= {
            "id", "email", "name", "avatar_url", "trust_level", "created_at",
        }, f"unexpected fields: {set(body.keys())}"

        # Hard-fail if any of the leaky fields ever reappear
        for forbidden in ("password_hash", "failed_login_attempts", "locked_until"):
            assert forbidden not in body, f"/me leaked {forbidden}: {body}"

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
