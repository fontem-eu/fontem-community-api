from __future__ import annotations

from contextvars import ContextVar

from src.infra.memory.mem_group_repo import InMemoryGroupRepository
from src.infra.memory.mem_issue_repo import InMemoryIssueRepository
from src.infra.memory.mem_moderation_repo import InMemoryModerationRepository
from src.infra.memory.mem_permission_repo import InMemoryPermissionRepository
from src.infra.memory.mem_report_repo import InMemoryReportRepository
from src.infra.memory.mem_user_repo import InMemoryUserRepository
from src.repositories.group_repository import GroupRepository
from src.repositories.issue_repository import IssueRepository
from src.repositories.moderation_repository import ModerationRepository
from src.repositories.permission_repository import PermissionRepository
from src.repositories.report_repository import ReportRepository
from src.repositories.user_repository import UserRepository
from src.services.issue_service import IssueService
from src.services.moderation_service import ModerationService
from src.services.permission_service import PermissionService
from src.services.report_service import ReportService

# Singleton in-memory repositories (shared across requests during app lifetime)
_group_repo: GroupRepository | None = None
_user_repo: UserRepository | None = None
_report_repo: ReportRepository | None = None
_permission_repo: PermissionRepository | None = None
_issue_repo: IssueRepository | None = None
_moderation_repo: ModerationRepository | None = None

# When True, each request gets repos backed by an AsyncSession from the pool
_use_postgres: bool = False
_pg_session_factory = None


def _init_defaults() -> None:
    global _group_repo, _user_repo, _report_repo, _permission_repo, _issue_repo, _moderation_repo
    if _group_repo is None:
        _group_repo = InMemoryGroupRepository()
    if _user_repo is None:
        _user_repo = InMemoryUserRepository()
    if _report_repo is None:
        _report_repo = InMemoryReportRepository()
    if _permission_repo is None:
        _permission_repo = InMemoryPermissionRepository(_group_repo)
    if _issue_repo is None:
        _issue_repo = InMemoryIssueRepository()
    if _moderation_repo is None:
        _moderation_repo = InMemoryModerationRepository()


def configure_postgres(
    user_repo: UserRepository | None = None,
    report_repo: ReportRepository | None = None,
    permission_repo: PermissionRepository | None = None,
    issue_repo: IssueRepository | None = None,
    group_repo: GroupRepository | None = None,
    moderation_repo: ModerationRepository | None = None,
    *,
    database_url: str | None = None,
) -> None:
    """Replace in-memory repos with Postgres implementations for production.

    Can be called in two ways:
      1. With explicit repo instances (for testing or custom wiring).
      2. With no repos + optional database_url to auto-create the async
         engine / session factory and build per-request Pg repos.
    """
    global _group_repo, _user_repo, _report_repo, _permission_repo
    global _issue_repo, _moderation_repo, _use_postgres, _pg_session_factory

    if user_repo is not None:
        # Explicit repo instances provided — use them directly
        _user_repo = user_repo
        _report_repo = report_repo
        _permission_repo = permission_repo
        _issue_repo = issue_repo
        _group_repo = group_repo
        _moderation_repo = moderation_repo
        return

    # Auto-configure: create engine + session factory from DATABASE_URL
    import os

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    url = database_url or os.environ["DATABASE_URL"]
    engine = create_async_engine(url, echo=False)
    _pg_session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    _use_postgres = True


def _make_pg_repos(session):  # type: ignore[no-untyped-def]
    """Build all Pg repo instances sharing a single AsyncSession."""
    from src.infra.postgres.pg_group_repo import PgGroupRepository
    from src.infra.postgres.pg_issue_repo import PgIssueRepository
    from src.infra.postgres.pg_moderation_repo import PgModerationRepository
    from src.infra.postgres.pg_permission_repo import PgPermissionRepository
    from src.infra.postgres.pg_report_repo import PgReportRepository
    from src.infra.postgres.pg_user_repo import PgUserRepository

    return {
        "user": PgUserRepository(session),
        "report": PgReportRepository(session),
        "permission": PgPermissionRepository(session),
        "issue": PgIssueRepository(session),
        "group": PgGroupRepository(session),
        "moderation": PgModerationRepository(session),
    }


_request_session: ContextVar = ContextVar("_request_session", default=None)


def _get_or_create_session():
    """Get a session for the current request context (async-safe via ContextVar)."""
    session = _request_session.get()
    if session is None or not session.is_active:
        assert _pg_session_factory is not None
        session = _pg_session_factory()
        _request_session.set(session)
    return session


def _pg_repos():
    """Create a set of Postgres repos sharing one session."""
    session = _get_or_create_session()
    return _make_pg_repos(session)


def get_user_repo() -> UserRepository:
    if _use_postgres:
        return _pg_repos()["user"]
    _init_defaults()
    assert _user_repo is not None
    return _user_repo


def get_report_repo() -> ReportRepository:
    if _use_postgres:
        return _pg_repos()["report"]
    _init_defaults()
    assert _report_repo is not None
    return _report_repo


def get_permission_repo() -> PermissionRepository:
    if _use_postgres:
        return _pg_repos()["permission"]
    _init_defaults()
    assert _permission_repo is not None
    return _permission_repo


def get_issue_repo() -> IssueRepository:
    if _use_postgres:
        return _pg_repos()["issue"]
    _init_defaults()
    assert _issue_repo is not None
    return _issue_repo


def get_group_repo() -> GroupRepository:
    if _use_postgres:
        return _pg_repos()["group"]
    _init_defaults()
    assert _group_repo is not None
    return _group_repo


def get_moderation_repo() -> ModerationRepository:
    if _use_postgres:
        return _pg_repos()["moderation"]
    _init_defaults()
    assert _moderation_repo is not None
    return _moderation_repo


def get_permission_service() -> PermissionService:
    return PermissionService(
        perms=get_permission_repo(),
        users=get_user_repo(),
        groups=get_group_repo(),
    )


def get_report_service() -> ReportService:
    return ReportService(
        reports=get_report_repo(),
        perms=get_permission_service(),
    )


def get_issue_service() -> IssueService:
    return IssueService(
        issues=get_issue_repo(),
        users=get_user_repo(),
    )


def get_moderation_service() -> ModerationService:
    return ModerationService(
        mod=get_moderation_repo(),
        users=get_user_repo(),
    )
