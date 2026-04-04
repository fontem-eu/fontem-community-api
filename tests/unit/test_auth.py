"""Tests for Google OAuth token exchange (AUTH-GOOGLE)."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from tests.conftest import make_headers


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

        # Login again with updated name
        updated_payload = {**_GOOGLE_PAYLOAD, "name": "Alice Updated"}
        with _patch_verify(updated_payload):
            resp2 = client.post("/auth/google", json={"credential": "fake-token-2"})
        assert resp2.status_code == 200
        assert resp2.json()["user"]["id"] == user_id_1
        assert resp2.json()["user"]["name"] == "Alice Updated"

    def test_banned_user_rejected(self, client, services):
        """Banned user gets 401 on Google login."""
        from src.domain.moderation import Sanction
        from datetime import datetime, timezone

        user_repo = services["user_repo"]

        # Create user first
        with _patch_verify():
            resp = client.post("/auth/google", json={"credential": "fake"})
        user_id = resp.json()["user"]["id"]

        # Ban the user (sync helper on InMemory repo)
        user_repo._add_sanction(Sanction(
            id="s1", user_id=user_id, type="ban",
            reason="test", applied_by="admin",
            starts_at=datetime.now(timezone.utc),
        ))

        # Try to login again
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
            side_effect=Exception("Invalid Google token"),
        ):
            # The endpoint catches HTTPException from verify, but if verify
            # raises a raw exception, FastAPI will return 500. The actual
            # verify function raises HTTPException(401) on bad tokens.
            pass

        from fastapi import HTTPException
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

        # Use the token to call /users/me
        me_resp = client.get("/users/me", headers={"Authorization": f"Bearer {token}"})
        assert me_resp.status_code == 200
        assert me_resp.json()["email"] == "alice@gmail.com"
