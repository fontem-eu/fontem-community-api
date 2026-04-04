from __future__ import annotations

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
    user_repo: UserRepository,
    report_repo: ReportRepository,
    permission_repo: PermissionRepository,
    issue_repo: IssueRepository,
    group_repo: GroupRepository,
    moderation_repo: ModerationRepository,
) -> None:
    """Replace in-memory repos with Postgres implementations for production."""
    global _group_repo, _user_repo, _report_repo, _permission_repo, _issue_repo, _moderation_repo
    _user_repo = user_repo
    _report_repo = report_repo
    _permission_repo = permission_repo
    _issue_repo = issue_repo
    _group_repo = group_repo
    _moderation_repo = moderation_repo


def get_user_repo() -> UserRepository:
    _init_defaults()
    assert _user_repo is not None
    return _user_repo


def get_report_repo() -> ReportRepository:
    _init_defaults()
    assert _report_repo is not None
    return _report_repo


def get_permission_repo() -> PermissionRepository:
    _init_defaults()
    assert _permission_repo is not None
    return _permission_repo


def get_issue_repo() -> IssueRepository:
    _init_defaults()
    assert _issue_repo is not None
    return _issue_repo


def get_group_repo() -> GroupRepository:
    _init_defaults()
    assert _group_repo is not None
    return _group_repo


def get_moderation_repo() -> ModerationRepository:
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
