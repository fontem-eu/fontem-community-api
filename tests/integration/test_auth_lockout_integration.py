"""Integration test for password lockout against real PostgreSQL.

Verifies the new ``failed_login_attempts`` and ``locked_until`` columns
on the ``users`` table behave correctly end-to-end through the API.
"""
from __future__ import annotations


def _register(client, email: str, password: str = "right-password-1") -> dict:
    """Register a fresh local account."""
    return client.post("/auth/register", json={
        "email": email,
        "password": password,
        "name": "LockoutTest",
    }).json()


class TestLoginLockoutIntegration:
    """AUTH-LOCKOUT-I01..I02: Lockout end-to-end against Postgres."""

    def test_5_wrong_passwords_lock_account(self, client):
        """5 wrong attempts → 6th (with right password) returns 429."""
        email = "lockout-int@test.gmr"
        _register(client, email)

        for _ in range(5):
            resp = client.post("/auth/login", json={
                "email": email,
                "password": "wrong",
            })
            assert resp.status_code == 401

        resp = client.post("/auth/login", json={
            "email": email,
            "password": "right-password-1",
        })
        assert resp.status_code == 429
        assert "locked" in resp.json()["detail"].lower()

    def test_successful_login_clears_counter(self, client):
        """Successful login resets the counter — can't get permanently locked."""
        email = "lockout-reset@test.gmr"
        _register(client, email)

        # 4 wrong (one short of lock)
        for _ in range(4):
            client.post("/auth/login", json={
                "email": email, "password": "wrong",
            })

        # Successful login
        ok = client.post("/auth/login", json={
            "email": email, "password": "right-password-1",
        })
        assert ok.status_code == 200

        # 4 more wrong attempts allowed without lockout
        for _ in range(4):
            r = client.post("/auth/login", json={
                "email": email, "password": "wrong",
            })
            assert r.status_code == 401
