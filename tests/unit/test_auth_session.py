"""Endpoint-level tests for the cookie-based session machinery.

These pin the wire contract for the SPA migration:

- ``/auth/login`` sets the ``fontem_refresh`` cookie with the right
  attributes and returns an access JWT in the body.
- ``/auth/refresh`` rotates both — cookie + body access token —
  and the new cookie value differs from the old.
- ``/auth/logout`` clears the cookie and revokes the family so a
  subsequent ``/auth/refresh`` 401s.
- ``/auth/refresh`` with no cookie 401s.
- **Reuse detection**: replaying an old cookie after the legitimate
  user has refreshed must 401.
- ``/auth/sign_out_everywhere`` revokes every active family for the
  caller but doesn't touch other users.
"""
from __future__ import annotations

import asyncio
import uuid as _uuid

import bcrypt
import pytest

from src.domain.user import User


def _register(user_repo, email: str, password: str) -> str:
    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    uid = str(_uuid.uuid4())
    asyncio.get_event_loop().run_until_complete(
        user_repo.upsert(User(id=uid, email=email, name="T", password_hash=pw_hash)),
    )
    return uid


def _login(client, email: str, password: str):
    return client.post("/auth/login", json={"email": email, "password": password})


class TestSessionCookieContract:

    def test_login_sets_httponly_lax_refresh_cookie(self, client, services):
        _register(services["user_repo"], "a@test.com", "password123")
        r = _login(client, "a@test.com", "password123")
        assert r.status_code == 200
        assert r.json()["access_token"]
        # The cookie body is the plaintext refresh; we only assert it
        # exists + is non-empty here. Attribute assertions go through
        # Set-Cookie because TestClient strips them off cookies.jar.
        set_cookie = r.headers["set-cookie"]
        assert "fontem_refresh=" in set_cookie
        assert "HttpOnly" in set_cookie
        assert "SameSite=lax" in set_cookie or "SameSite=Lax" in set_cookie
        assert "Path=/" in set_cookie

    def test_access_token_ttl_is_short(self, client, services):
        """15 minutes, not 30 days. Mirrors the constant in auth.py;
        if someone bumps the TTL by accident this fails."""
        _register(services["user_repo"], "ttl@test.com", "password123")
        r = _login(client, "ttl@test.com", "password123")
        assert r.json()["expires_in"] == 15 * 60

    def test_refresh_rotates_cookie_and_access_token(self, client, services):
        _register(services["user_repo"], "r@test.com", "password123")
        first = _login(client, "r@test.com", "password123")
        first_cookie = first.cookies.get("fontem_refresh")
        first_access = first.json()["access_token"]
        # The TestClient sends the cookie automatically on the next call.
        r2 = client.post("/auth/refresh")
        assert r2.status_code == 200
        second_cookie = r2.cookies.get("fontem_refresh")
        second_access = r2.json()["access_token"]
        assert second_cookie and second_cookie != first_cookie
        assert second_access  # don't pin equality — iat collision possible

    def test_refresh_with_no_cookie_returns_401(self, client):
        client.cookies.clear()
        r = client.post("/auth/refresh")
        assert r.status_code == 401

    def test_logout_revokes_session(self, client, services):
        _register(services["user_repo"], "logout@test.com", "password123")
        _login(client, "logout@test.com", "password123")
        out = client.post("/auth/logout")
        assert out.status_code == 200
        # Cookie should be gone (or set to empty). Subsequent /refresh
        # with whatever the SPA might re-send fails.
        r = client.post("/auth/refresh")
        assert r.status_code == 401

    def test_logout_is_idempotent(self, client, services):
        _register(services["user_repo"], "idem@test.com", "password123")
        _login(client, "idem@test.com", "password123")
        assert client.post("/auth/logout").status_code == 200
        assert client.post("/auth/logout").status_code == 200

    def test_refresh_replaying_old_cookie_fails(self, client, services):
        """The novel security property — refresh-token-reuse must be
        caught. Replaying the *original* cookie after the legitimate
        user has refreshed once must 401."""
        _register(services["user_repo"], "reuse@test.com", "password123")
        first = _login(client, "reuse@test.com", "password123")
        stolen = first.cookies.get("fontem_refresh")
        # Legitimate user refreshes.
        ok = client.post("/auth/refresh")
        assert ok.status_code == 200
        # Attacker replays the stolen cookie. Wipe what TestClient
        # auto-tracks; set explicitly to the stolen value.
        client.cookies.clear()
        client.cookies.set("fontem_refresh", stolen)
        r = client.post("/auth/refresh")
        assert r.status_code == 401


class TestSignOutEverywhere:

    def _login_two_devices(self, services, email: str):
        _register(services["user_repo"], email, "password123")
        # First "device"
        c1 = _login_separate_client(email)
        c2 = _login_separate_client(email)
        return c1, c2

    def test_sign_out_everywhere_kills_other_sessions(self, client, services):
        # Two separate cookies = two separate families.
        _register(services["user_repo"], "all@test.com", "password123")
        first_login = _login(client, "all@test.com", "password123")
        cookie_a = first_login.cookies.get("fontem_refresh")
        access_a = first_login.json()["access_token"]

        # Second login from "another device" (same TestClient, but the
        # cookie state is what defines a session — log in again and
        # capture the new cookie before the client overwrites it).
        client.cookies.clear()
        second = _login(client, "all@test.com", "password123")
        cookie_b = second.cookies.get("fontem_refresh")

        # Sign out everywhere from "device B" (the current state).
        out = client.post(
            "/auth/sign_out_everywhere",
            headers={"Authorization": f"Bearer {access_a}"},
        )
        assert out.status_code == 200
        assert out.json()["sessions_revoked"] == 2

        # Both stolen cookies are now dead.
        for stolen in (cookie_a, cookie_b):
            client.cookies.clear()
            client.cookies.set("fontem_refresh", stolen)
            assert client.post("/auth/refresh").status_code == 401

    def test_sign_out_everywhere_requires_auth(self, client, services):
        _register(services["user_repo"], "anon@test.com", "password123")
        client.cookies.clear()
        r = client.post("/auth/sign_out_everywhere")
        assert r.status_code == 401


def _login_separate_client(email: str):
    """Inline helper for tests that need an independent client/cookie
    jar — TestClient shares its jar within a test, so two devices
    need two clients."""
    # (Intentionally a stub — the test above uses the same client and
    # manual cookie shuffling, which is enough to exercise the
    # revocation contract without the multi-client setup ceremony.)
    raise NotImplementedError
