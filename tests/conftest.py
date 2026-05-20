"""
Shared fixtures for unit tests.
All tests use InMemory repositories — 0 I/O, sub-millisecond.
"""
# pylint: disable=redefined-outer-name
# ── pytest fixtures shadow the fixture-name parameter on every test
#    that consumes them; that's the canonical pytest pattern, not a
#    name-collision bug. Disable module-wide rather than per-line.
from __future__ import annotations

import uuid

import pytest
from dishka.integrations.fastapi import setup_dishka
from jose import jwt
from starlette.testclient import TestClient

from src.api.app import app
from src.api.auth import JWT_ALGORITHM, JWT_SECRET
from src.api.rate_limit import limiter
from src.domain.user import User
from src.infra.memory.mem_group_repo import InMemoryGroupRepository
from src.infra.memory.mem_issue_repo import InMemoryIssueRepository
from src.infra.memory.mem_moderation_repo import InMemoryModerationRepository
from src.infra.memory.mem_permission_repo import InMemoryPermissionRepository
from src.infra.memory.mem_report_repo import InMemoryReportRepository
from src.infra.memory.mem_tag_follow_repo import InMemoryTagFollowRepository
from src.infra.memory.mem_user_repo import InMemoryUserRepository
from src.services.issue_service import IssueService
from src.services.moderation_service import ModerationService
from src.services.permission_service import PermissionService
from src.services.report_service import ReportService
from src.services.tag_service import TagService
from tests.dishka_fixtures import make_test_container

# Disable per-endpoint rate limiting in tests so bursts don't trip auth limits.
limiter.enabled = False


def _stable_uuid(raw_id: str) -> str:
    """Convert a human-friendly test ID to the UUID5 that auth.py will derive.

    The auth middleware converts any non-UUID sub to UUID5. Tests must seed
    users with the same derived ID so lookups match.
    """
    try:
        uuid.UUID(raw_id)
        return raw_id  # already a valid UUID
    except ValueError:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, raw_id))


def make_token(user_id: str = "user-1", email: str = "test@test.com",
               name: str = "Test User") -> str:
    return jwt.encode(
        {"sub": user_id, "email": email, "name": name},
        JWT_SECRET, algorithm=JWT_ALGORITHM,
    )


def make_headers(user_id: str = "user-1", **kwargs) -> dict:
    return {"Authorization": f"Bearer {make_token(user_id, **kwargs)}"}


@pytest.fixture()
def services():
    """Create a fresh set of InMemory services for each test."""
    user_repo = InMemoryUserRepository()
    group_repo = InMemoryGroupRepository()
    report_repo = InMemoryReportRepository()
    permission_repo = InMemoryPermissionRepository(group_repo, report_repo)
    issue_repo = InMemoryIssueRepository()
    mod_repo = InMemoryModerationRepository()

    tag_follow_repo = InMemoryTagFollowRepository()

    perm_svc = PermissionService(permission_repo, user_repo, group_repo)
    report_svc = ReportService(report_repo, perm_svc)
    issue_svc = IssueService(issue_repo, user_repo)
    mod_svc = ModerationService(mod_repo, user_repo)
    tag_svc = TagService(report_repo, tag_follow_repo, perm_svc)

    return {
        "user_repo": user_repo,
        "group_repo": group_repo,
        "report_repo": report_repo,
        "permission_repo": permission_repo,
        "issue_repo": issue_repo,
        "mod_repo": mod_repo,
        "tag_follow_repo": tag_follow_repo,
        "perm_svc": perm_svc,
        "report_svc": report_svc,
        "issue_svc": issue_svc,
        "mod_svc": mod_svc,
        "tag_svc": tag_svc,
    }


@pytest.fixture()
def client(services):
    """TestClient with both dishka container and legacy dependency_overrides.

    Dishka handles FromDishka[T] injection (auth, migrated endpoints).
    Old Depends(get_*) overrides handle endpoints not yet migrated.
    """
    # Reset middleware stack so setup_dishka can add its middleware
    app.middleware_stack = None

    container = make_test_container(services)
    setup_dishka(container, app)

    with TestClient(app) as c:
        yield c

    app.middleware_stack = None


async def seed_user(user_repo: InMemoryUserRepository, user_id: str,
                    trust_level: str = "contributor",
                    roles: list[str] | None = None) -> User:
    """Create a user with given trust level and roles.

    Derives a UUID5 from human-friendly IDs (e.g. "user-1" → UUID5) to match
    what the auth middleware produces. The returned User has the derived ID.
    All tests should use the returned user.id (or _stable_uuid(label)) when
    calling services directly.
    """
    derived = _stable_uuid(user_id)
    user = User(id=derived, email=f"{user_id}@test.com", name=user_id,
                trust_level=trust_level)
    user = await user_repo.upsert(user)
    if roles:
        await user_repo.set_roles(derived, roles)
    return user
