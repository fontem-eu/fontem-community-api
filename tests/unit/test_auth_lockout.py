"""Tests for password lockout (AUTH-LOCKOUT)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.domain.user import User


def _register_user(user_repo, email: str, password: str) -> str:
    """Hash a password via bcrypt and seed a user. Returns the user id."""
    import bcrypt
    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    import uuid as _uuid
    uid = str(_uuid.uuid4())
    user = User(id=uid, email=email, name="Test", password_hash=pw_hash)
    # Use the sync helper since fixtures expose async repo methods only
    import asyncio
    asyncio.get_event_loop().run_until_complete(user_repo.upsert(user))
    return uid


class TestLoginLockout:
    """AUTH-LOCKOUT: Account-level brute-force protection."""

    def test_5_wrong_passwords_lock_account(self, client, services):
        """5 consecutive wrong passwords lock the account for 15 minutes."""
        user_repo = services["user_repo"]
        _register_user(user_repo, "lock@test.com", "correct-password")

        for _ in range(5):
            resp = client.post("/auth/login", json={
                "email": "lock@test.com",
                "password": "wrong",
            })
            assert resp.status_code == 401

        # 6th attempt — even with correct password — must be blocked by lockout
        resp = client.post("/auth/login", json={
            "email": "lock@test.com",
            "password": "correct-password",
        })
        assert resp.status_code == 429
        assert "locked" in resp.json()["detail"].lower()

    def test_successful_login_clears_failed_counter(self, client, services):
        """A successful login resets the failed-attempt counter."""
        user_repo = services["user_repo"]
        _register_user(user_repo, "reset@test.com", "right")

        # 4 wrong attempts (one short of lockout)
        for _ in range(4):
            client.post("/auth/login", json={
                "email": "reset@test.com",
                "password": "wrong",
            })

        # Successful login resets counter
        resp = client.post("/auth/login", json={
            "email": "reset@test.com",
            "password": "right",
        })
        assert resp.status_code == 200

        # 4 more wrong attempts after success — should not lock yet
        for _ in range(4):
            r = client.post("/auth/login", json={
                "email": "reset@test.com",
                "password": "wrong",
            })
            assert r.status_code == 401

        # 5th wrong attempt finally locks
        r = client.post("/auth/login", json={
            "email": "reset@test.com",
            "password": "wrong",
        })
        assert r.status_code == 401
        # And now the account is locked
        r = client.post("/auth/login", json={
            "email": "reset@test.com",
            "password": "right",
        })
        assert r.status_code == 429

    def test_unknown_email_does_not_leak(self, client):
        """Wrong attempts on unknown emails return generic 401, never 429."""
        for _ in range(10):
            resp = client.post("/auth/login", json={
                "email": "nobody@nowhere.com",
                "password": "anything",
            })
            assert resp.status_code == 401
            assert resp.json()["detail"] == "Invalid email or password"

    def test_lockout_expires(self, client, services):
        """A lock with expired ``locked_until`` should not block login."""
        user_repo = services["user_repo"]
        uid = _register_user(user_repo, "expired@test.com", "right")

        # Manually expire a lockout in the past
        user = user_repo._users[uid]
        user.failed_login_attempts = 5
        user.locked_until = datetime.now(timezone.utc) - timedelta(minutes=1)

        resp = client.post("/auth/login", json={
            "email": "expired@test.com",
            "password": "right",
        })
        assert resp.status_code == 200
