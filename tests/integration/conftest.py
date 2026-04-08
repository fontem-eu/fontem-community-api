"""
Integration test fixtures — tests against deployed gmr-community-api + PostgreSQL.

Talks to the real running API (not TestClient) to test the full stack
including async SQLAlchemy, middleware, and Postgres.
"""
from __future__ import annotations

import os
import uuid

import httpx
import pytest
from jose import jwt

# JWT secret must match the deployed app
JWT_SECRET = os.environ.get("JWT_SECRET", "gmr-community-jwt-secret-2026")
JWT_ALGORITHM = "HS256"
CAPI_URL = os.environ.get("CAPI_URL", "http://gmr-community-api.gmr.svc.cluster.local:8001")


def make_token(user_id: str | None = None, email: str | None = None,
               name: str = "Test User") -> str:
    uid = user_id or str(uuid.uuid4())
    if email is None:
        email = f"{uid[:8]}@test.gmr"
    return jwt.encode(
        {"sub": uid, "email": email, "name": name},
        JWT_SECRET, algorithm=JWT_ALGORITHM,
    )


def make_headers(user_id: str | None = None, **kwargs) -> dict:
    return {"Authorization": f"Bearer {make_token(user_id, **kwargs)}"}


@pytest.fixture()
def client():
    """HTTP client for integration tests — talks to the deployed API."""
    with httpx.Client(base_url=CAPI_URL, timeout=30.0) as c:
        yield c


@pytest.fixture()
def user_id():
    """Fresh UUID for test user isolation."""
    return str(uuid.uuid4())


@pytest.fixture()
def user2_id():
    """Second user for permission tests."""
    return str(uuid.uuid4())
