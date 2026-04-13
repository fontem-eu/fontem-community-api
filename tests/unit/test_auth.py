"""Tests for Google OAuth token exchange (AUTH-GOOGLE)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from src.domain.moderation import Sanction


# Fake Google payload returned after token verification
_GOOGLE_PAYLOAD = {
    "sub": "1234567890",
    "email": "alice@gmail.com",
    "email_verified": True,
    "name": "Alice Test",
    "picture": "https://lh3.googleusercontent.com/photo.jpg",
    "iss": "accounts.google.com",
    "aud": "test-client-id",
}


def _patch_verify(payload=None):
    """Patch _verify_google_token to return a fake payload."""
    return patch(
        "src.api.routers.auth._verify_google_token",
        new_callable=AsyncMock,
        return_value=payload or _GOOGLE_PAYLOAD,
    )


class TestGoogleAuth:
    """AUTH-GOOGLE: Google OAuth token exchange."""

    def test_new_user_created(self, client):
        """First Google login creates a new user and returns a JWT."""
        with _patch_verify():
            resp = client.post("/auth/google", json={"credential": "fake-id-token"})
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert data["user"]["email"] == "alice@gmail.com"
        assert data["user"]["name"] == "Alice Test"
        assert data["user"]["avatar_url"] == "https://lh3.googleusercontent.com/photo.jpg"

    def test_existing_user_updated(self, client):
        """Second login updates name/avatar and returns the same user."""
        with _patch_verify():
            resp1 = client.post("/auth/google", json={"credential": "fake-token-1"})
        user_id_1 = resp1.json()["user"]["id"]

        updated_payload = {**_GOOGLE_PAYLOAD, "name": "Alice Updated"}
        with _patch_verify(updated_payload):
            resp2 = client.post("/auth/google", json={"credential": "fake-token-2"})
        assert resp2.status_code == 200
        assert resp2.json()["user"]["id"] == user_id_1
        assert resp2.json()["user"]["name"] == "Alice Updated"

    def test_banned_user_rejected(self, client, services):
        """Banned user gets 401 on Google login."""
        user_repo = services["user_repo"]

        with _patch_verify():
            resp = client.post("/auth/google", json={"credential": "fake"})
        user_id = resp.json()["user"]["id"]

        # Ban the user via the repo's test helper
        user_repo.add_sanction_sync(Sanction(
            id="s1", user_id=user_id, type="ban",
            reason="test", applied_by="admin",
            starts_at=datetime.now(timezone.utc),
        ))

        with _patch_verify():
            resp = client.post("/auth/google", json={"credential": "fake"})
        assert resp.status_code == 401
        assert "banned" in resp.json()["detail"].lower()

    def test_missing_credential(self, client):
        """Missing credential field returns 422."""
        resp = client.post("/auth/google", json={})
        assert resp.status_code == 422

    def test_invalid_token_returns_401(self, client):
        """Invalid Google token returns 401."""
        with patch(
            "src.api.routers.auth._verify_google_token",
            new_callable=AsyncMock,
            side_effect=HTTPException(status_code=401, detail="Invalid Google token"),
        ):
            resp = client.post("/auth/google", json={"credential": "bad-token"})
        assert resp.status_code == 401

    def test_jwt_is_valid(self, client):
        """Returned JWT can be used to call authenticated endpoints."""
        with _patch_verify():
            resp = client.post("/auth/google", json={"credential": "fake"})
        token = resp.json()["access_token"]

        me_resp = client.get("/users/me", headers={"Authorization": f"Bearer {token}"})
        assert me_resp.status_code == 200
        assert me_resp.json()["email"] == "alice@gmail.com"

    def test_user_id_is_valid_uuid(self, client):
        """Regression: user ID must be a valid UUID for PostgreSQL compatibility."""
        with _patch_verify():
            resp = client.post("/auth/google", json={"credential": "fake"})
        user_id = resp.json()["user"]["id"]
        # Must not raise ValueError
        parsed = uuid.UUID(user_id)
        assert str(parsed) == user_id

    def test_user_id_is_deterministic(self, client):
        """Same Google sub always produces the same UUID."""
        with _patch_verify():
            resp1 = client.post("/auth/google", json={"credential": "fake"})
        with _patch_verify():
            resp2 = client.post("/auth/google", json={"credential": "fake"})
        assert resp1.json()["user"]["id"] == resp2.json()["user"]["id"]


class TestGoogleAuthMalformedTokens:
    """Regression: malformed Google credentials must return 401, never 500.

    These tests do NOT mock _verify_google_token — they exercise the real
    header parsing to ensure it handles garbage input gracefully.
    """

    def test_garbage_string_returns_401(self, client):
        """A non-JWT string must not crash the server."""
        resp = client.post("/auth/google", json={"credential": "not-a-jwt"})
        assert resp.status_code == 401

    def test_binary_noise_returns_401(self, client):
        """Base64-decodable but non-JSON content must not crash."""
        import base64
        noise = base64.urlsafe_b64encode(b"\xa9\x00\xff").decode()
        resp = client.post("/auth/google", json={"credential": f"{noise}.x.y"})
        assert resp.status_code == 401

    def test_empty_credential_returns_401(self, client):
        """Empty string credential must not crash."""
        resp = client.post("/auth/google", json={"credential": ""})
        assert resp.status_code == 401

    def test_single_segment_returns_401(self, client):
        """A string with no dots (not a JWT) must return 401."""
        resp = client.post("/auth/google", json={"credential": "onesinglesegment"})
        assert resp.status_code == 401

    def test_two_segments_returns_401(self, client):
        """Two dot-separated segments (malformed JWT) must return 401."""
        resp = client.post("/auth/google", json={"credential": "header.payload"})
        assert resp.status_code == 401
