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
    "aud": "test-dishka_client-id",
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

    def test_new_user_created(self, dishka_client):
        """First Google login creates a new user and returns a JWT."""
        with _patch_verify():
            resp = dishka_client.post("/auth/google", json={"credential": "fake-id-token"})
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert data["user"]["email"] == "alice@gmail.com"
        assert data["user"]["name"] == "Alice Test"
        assert data["user"]["avatar_url"] == "https://lh3.googleusercontent.com/photo.jpg"

    def test_existing_user_updated(self, dishka_client):
        """Second login updates name/avatar and returns the same user."""
        with _patch_verify():
            resp1 = dishka_client.post("/auth/google", json={"credential": "fake-token-1"})
        user_id_1 = resp1.json()["user"]["id"]

        updated_payload = {**_GOOGLE_PAYLOAD, "name": "Alice Updated"}
        with _patch_verify(updated_payload):
            resp2 = dishka_client.post("/auth/google", json={"credential": "fake-token-2"})
        assert resp2.status_code == 200
        assert resp2.json()["user"]["id"] == user_id_1
        assert resp2.json()["user"]["name"] == "Alice Updated"

    def test_banned_user_rejected(self, dishka_client, services):
        """Banned user gets 401 on Google login."""
        user_repo = services["user_repo"]

        with _patch_verify():
            resp = dishka_client.post("/auth/google", json={"credential": "fake"})
        user_id = resp.json()["user"]["id"]

        # Ban the user via the repo's test helper
        user_repo.add_sanction_sync(Sanction(
            id="s1", user_id=user_id, type="ban",
            reason="test", applied_by="admin",
            starts_at=datetime.now(timezone.utc),
        ))

        with _patch_verify():
            resp = dishka_client.post("/auth/google", json={"credential": "fake"})
        assert resp.status_code == 401
        assert "banned" in resp.json()["detail"].lower()

    def test_missing_credential(self, dishka_client):
        """Missing credential field returns 422."""
        resp = dishka_client.post("/auth/google", json={})
        assert resp.status_code == 422

    def test_invalid_token_returns_401(self, dishka_client):
        """Invalid Google token returns 401."""
        with patch(
            "src.api.routers.auth._verify_google_token",
            new_callable=AsyncMock,
            side_effect=HTTPException(status_code=401, detail="Invalid Google token"),
        ):
            resp = dishka_client.post("/auth/google", json={"credential": "bad-token"})
        assert resp.status_code == 401

    def test_jwt_is_valid(self, dishka_client):
        """Returned JWT can be used to call authenticated endpoints."""
        with _patch_verify():
            resp = dishka_client.post("/auth/google", json={"credential": "fake"})
        token = resp.json()["access_token"]

        me_resp = dishka_client.get("/users/me", headers={"Authorization": f"Bearer {token}"})
        assert me_resp.status_code == 200
        assert me_resp.json()["email"] == "alice@gmail.com"

    def test_user_id_is_valid_uuid(self, dishka_client):
        """Regression: user ID must be a valid UUID for PostgreSQL compatibility."""
        with _patch_verify():
            resp = dishka_client.post("/auth/google", json={"credential": "fake"})
        user_id = resp.json()["user"]["id"]
        # Must not raise ValueError
        parsed = uuid.UUID(user_id)
        assert str(parsed) == user_id

    def test_user_id_is_deterministic(self, dishka_client):
        """Same Google sub always produces the same UUID."""
        with _patch_verify():
            resp1 = dishka_client.post("/auth/google", json={"credential": "fake"})
        with _patch_verify():
            resp2 = dishka_client.post("/auth/google", json={"credential": "fake"})
        assert resp1.json()["user"]["id"] == resp2.json()["user"]["id"]
