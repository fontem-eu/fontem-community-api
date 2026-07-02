"""
Shared fixtures for unit tests.
All tests use InMemory repositories — 0 I/O, sub-millisecond.
"""
# pylint: disable=redefined-outer-name
# ── pytest fixtures shadow the fixture-name parameter on every test
#    that consumes them; that's the canonical pytest pattern, not a
#    name-collision bug. Disable module-wide rather than per-line.
from __future__ import annotations

import os
import uuid

# Starlette's TestClient speaks plain http; a Secure cookie wouldn't
# round-trip. Set BEFORE the app + handlers see their first request.
os.environ.setdefault("FONTEM_COOKIE_INSECURE", "1")

import pytest
from dishka.integrations.fastapi import setup_dishka
from jose import jwt
from starlette.testclient import TestClient

from src.api.app import app
from src.api.auth import JWT_ALGORITHM, JWT_SECRET
from src.api.rate_limit import limiter
from src.domain.user import User
from src.infra.memory.mem_group_repo import InMemoryGroupRepository
from src.infra.memory.mem_investigation_repo import InMemoryInvestigationRepository
from src.infra.memory.mem_dossier_repo import InMemoryDossierRepository
from src.infra.memory.mem_visualization_repo import InMemoryVisualizationRepository
from src.infra.memory.mem_data_project_repo import InMemoryDataProjectRepository
from src.infra.memory.mem_resource_grant_repo import InMemoryResourceGrantRepository
from src.infra.memory.mem_issue_repo import InMemoryIssueRepository
from src.infra.memory.mem_activity_repo import InMemoryActivityRepository
from src.infra.memory.mem_moderation_repo import InMemoryModerationRepository
from src.infra.memory.mem_permission_repo import InMemoryPermissionRepository
from src.infra.memory.mem_authz_audit_repo import InMemoryAuthzAuditRepository
from src.infra.memory.mem_report_repo import InMemoryReportRepository
from src.infra.memory.mem_flower_repo import InMemoryFlowerRepository
from src.infra.memory.mem_refresh_token_repo import InMemoryRefreshTokenRepository
from src.infra.memory.mem_auth_token_repo import InMemoryAuthTokenRepository
from src.infra.memory.mem_tag_follow_repo import InMemoryTagFollowRepository
from src.infra.memory.mem_user_repo import InMemoryUserRepository
from src.services.issue_service import IssueService
from src.services.activity_service import ActivityService
from src.services.moderation_service import ModerationService
from src.services.permission_service import PermissionService
from src.services.authz import AuthorizationService
from src.services.authz.audit import AuditLogger
from src.services.group_service import GroupService
from src.services.investigation_service import InvestigationService
from src.services.dossier_service import DossierService
from src.services.access_inheritance import AccessInheritance
from src.services.visualization_service import VisualizationService
from src.services.data_project_service import DataProjectService
from src.services.report_service import ReportService
from src.services.flower_service import FlowerService
from src.services.refresh_token_service import RefreshTokenService
from src.services.mail_service import MailService
from src.services.email_verification_service import EmailVerificationService
from src.services.password_reset_service import PasswordResetService
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
    authz_audit_repo = InMemoryAuthzAuditRepository()

    tag_follow_repo = InMemoryTagFollowRepository()
    flower_repo = InMemoryFlowerRepository()
    refresh_token_repo = InMemoryRefreshTokenRepository()
    auth_token_repo = InMemoryAuthTokenRepository()

    authz_svc = AuthorizationService(users=user_repo, audit=AuditLogger(authz_audit_repo))
    perm_svc = PermissionService(permission_repo, user_repo, group_repo)
    group_svc = GroupService(group_repo, user_repo, authz_svc)
    investigation_repo = InMemoryInvestigationRepository()
    dossier_repo = InMemoryDossierRepository()
    visualization_repo = InMemoryVisualizationRepository()
    data_project_repo = InMemoryDataProjectRepository()
    resource_grant_repo = InMemoryResourceGrantRepository()
    activity_repo = InMemoryActivityRepository()
    activity_svc = ActivityService(activity_repo)
    investigation_svc = InvestigationService(
        investigation_repo, user_repo, authz_svc, report_repo, dossier_repo, visualization_repo,
        activity_svc)
    dossier_svc = DossierService(dossier_repo, report_repo, authz_svc, investigation_repo, resource_grant_repo, user_repo, activity_svc)
    visualization_svc = VisualizationService(visualization_repo, investigation_repo, authz_svc, resource_grant_repo, user_repo)
    data_project_svc = DataProjectService(data_project_repo)
    inheritance = AccessInheritance(investigation_repo, dossier_repo)
    report_svc = ReportService(report_repo, perm_svc, authz_svc, inheritance, user_repo, group_repo, activity_svc)
    issue_svc = IssueService(issue_repo, user_repo, authz_svc, activity_svc)
    mod_svc = ModerationService(mod_repo, user_repo, authz_svc)
    tag_svc = TagService(report_repo, tag_follow_repo, perm_svc, authz_svc)
    flower_svc = FlowerService(flower_repo, report_repo, authz_svc)
    refresh_token_svc = RefreshTokenService(refresh_token_repo)
    mail_svc = MailService()  # MAIL_SUPPRESS defaults true → no real sends
    email_verify_svc = EmailVerificationService(auth_token_repo, user_repo, mail_svc)
    password_reset_svc = PasswordResetService(
        auth_token_repo, user_repo, mail_svc, refresh_token_svc)

    return {
        "user_repo": user_repo,
        "group_repo": group_repo,
        "investigation_repo": investigation_repo,
        "investigation_svc": investigation_svc,
        "dossier_repo": dossier_repo,
        "dossier_svc": dossier_svc,
        "visualization_repo": visualization_repo,
        "resource_grant_repo": resource_grant_repo,
        "visualization_svc": visualization_svc,
        "data_project_repo": data_project_repo,
        "data_project_svc": data_project_svc,
        "report_repo": report_repo,
        "permission_repo": permission_repo,
        "issue_repo": issue_repo,
        "activity_repo": activity_repo,
        "activity_svc": activity_svc,
        "mod_repo": mod_repo,
        "tag_follow_repo": tag_follow_repo,
        "flower_repo": flower_repo,
        "refresh_token_repo": refresh_token_repo,
        "auth_token_repo": auth_token_repo,
        "authz_audit_repo": authz_audit_repo,
        "authz_svc": authz_svc,
        "perm_svc": perm_svc,
        "group_svc": group_svc,
        "report_svc": report_svc,
        "issue_svc": issue_svc,
        "mod_svc": mod_svc,
        "tag_svc": tag_svc,
        "flower_svc": flower_svc,
        "refresh_token_svc": refresh_token_svc,
        "mail_svc": mail_svc,
        "email_verify_svc": email_verify_svc,
        "password_reset_svc": password_reset_svc,
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
                    roles: list[str] | None = None,
                    email_verified: bool = True) -> User:
    """Create a user with given trust level and roles.

    Derives a UUID5 from human-friendly IDs (e.g. "user-1" → UUID5) to match
    what the auth middleware produces. The returned User has the derived ID.
    All tests should use the returned user.id (or _stable_uuid(label)) when
    calling services directly.
    """
    derived = _stable_uuid(user_id)
    # Default to verified — most tests model established accounts. The
    # "Required" email-verification gate is exercised explicitly in
    # test_email_verification.py with email_verified=False.
    from datetime import datetime, timezone
    user = User(id=derived, email=f"{user_id}@test.com", name=user_id,
                trust_level=trust_level,
                email_verified_at=(datetime.now(timezone.utc) if email_verified else None))
    user = await user_repo.upsert(user)
    if roles:
        await user_repo.set_roles(derived, roles)
    return user
