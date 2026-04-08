"""
Integration test fixtures — runs against real PostgreSQL.

Requires DATABASE_URL env var or defaults to the gmr cluster Postgres.
"""
from __future__ import annotations

import os
import uuid

# Set env vars BEFORE importing the app (JWT_SECRET is read at import time)
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://postgres:gmr-pg-2026@postgresql.gmr.svc.cluster.local:5432/gmr_app")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-for-integration")

import pytest
from jose import jwt
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from starlette.testclient import TestClient

from src.api.app import app
from src.api.auth import JWT_SECRET, JWT_ALGORITHM
from src.api.dependencies import configure_postgres
from src.infra.postgres.pg_group_repo import PgGroupRepository
from src.infra.postgres.pg_issue_repo import PgIssueRepository
from src.infra.postgres.pg_moderation_repo import PgModerationRepository
from src.infra.postgres.pg_permission_repo import PgPermissionRepository
from src.infra.postgres.pg_report_repo import PgReportRepository
from src.infra.postgres.pg_user_repo import PgUserRepository

DB_URL = os.environ["DATABASE_URL"]


def make_token(user_id: str | None = None, email: str = "test@test.com",
               name: str = "Test User") -> str:
    uid = user_id or str(uuid.uuid4())
    return jwt.encode(
        {"sub": uid, "email": email, "name": name},
        JWT_SECRET, algorithm=JWT_ALGORITHM,
    )


def make_headers(user_id: str | None = None, **kwargs) -> dict:
    return {"Authorization": f"Bearer {make_token(user_id, **kwargs)}"}


@pytest.fixture()
def db_session():
    """Create a fresh async session for direct repo testing."""
    import asyncio
    engine = create_async_engine(DB_URL)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    session = asyncio.get_event_loop().run_until_complete(factory().__aenter__())
    yield session
    # Rollback to keep test DB clean
    asyncio.get_event_loop().run_until_complete(session.rollback())
    asyncio.get_event_loop().run_until_complete(session.close())
    asyncio.get_event_loop().run_until_complete(engine.dispose())


@pytest.fixture()
def repos(db_session):
    """Provide all Pg repos sharing one session."""
    return {
        "user": PgUserRepository(db_session),
        "report": PgReportRepository(db_session),
        "permission": PgPermissionRepository(db_session),
        "issue": PgIssueRepository(db_session),
        "group": PgGroupRepository(db_session),
        "moderation": PgModerationRepository(db_session),
        "session": db_session,
    }


@pytest.fixture()
def client():
    """TestClient wired to real PostgreSQL (via app lifespan)."""
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def user_id():
    """Generate a fresh UUID for test user isolation."""
    return str(uuid.uuid4())


@pytest.fixture()
def user2_id():
    """Second user for permission tests."""
    return str(uuid.uuid4())
