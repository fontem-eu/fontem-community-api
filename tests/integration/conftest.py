"""
Integration test fixtures — in-process API against a testcontainers Postgres.

No cluster dependency. The FastAPI app runs via Starlette TestClient against
a disposable Postgres container spun up once per test session.
"""
from __future__ import annotations

import os
import uuid

import pytest
from jose import jwt
from testcontainers.postgres import PostgresContainer

# ── JWT helpers ──────────────────────────────────────────────

# Must match src/api/auth.py default
JWT_SECRET = os.environ.get("JWT_SECRET", "dev-secret-do-not-use-in-production")
JWT_ALGORITHM = "HS256"

# Disable per-endpoint rate limiting in tests so bursts don't trip auth limits.
from src.api.rate_limit import limiter as _limiter
_limiter.enabled = False


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


# ── Postgres + App (session-scoped) ──────────────────────────

@pytest.fixture(scope="session")
def _postgres():
    """Disposable Postgres container for the test session."""
    pg = PostgresContainer("postgres:16-alpine")
    pg.start()
    sync_url = pg.get_connection_url()
    async_url = sync_url.replace("psycopg2", "asyncpg")

    # Create schema from ORM models
    from sqlalchemy import create_engine
    from src.infra.postgres.models import Base
    engine = create_engine(sync_url)
    Base.metadata.create_all(engine)
    engine.dispose()

    # Set env so the app (imported by tests/conftest.py) picks it up.
    # This only works if integration tests are collected AFTER the
    # global conftest, which pytest guarantees for sub-directory conftest.
    os.environ["DATABASE_URL"] = async_url

    yield pg
    pg.stop()


@pytest.fixture(scope="session")
def _test_client(_postgres):
    """Session-scoped TestClient — lifespan fires once, not per test."""
    from starlette.testclient import TestClient
    from src.api.app import build_app
    application = build_app(os.environ["DATABASE_URL"])
    with TestClient(application, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def client(_test_client):
    """Per-test alias — shares the session-scoped TestClient."""
    return _test_client


@pytest.fixture()
def user_id():
    """Fresh UUID for test user isolation."""
    return str(uuid.uuid4())


@pytest.fixture()
def user2_id():
    """Second user for permission tests."""
    return str(uuid.uuid4())
