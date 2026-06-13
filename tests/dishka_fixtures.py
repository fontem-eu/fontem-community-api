"""Dishka test infrastructure — InMemory providers for unit tests.

Provides the same in-memory repos and services as the old
``dependency_overrides`` approach, but through dishka's container so
endpoints using ``FromDishka[T]`` get proper injection.

The ``dishka_client`` fixture coexists with the old ``client`` fixture
during the migration: old endpoints still use ``dependency_overrides``,
newly migrated ones use dishka. Once all endpoints are migrated,
``dependency_overrides`` can be removed entirely.
"""
from __future__ import annotations

from dishka import Provider, Scope, provide, make_async_container

from src.assistant.proxy_client import ClaudeProxyClient
from src.assistant.repository import AssistRepository, InMemoryAssistRepository
from src.assistant.service import AssistantService
from src.assistant.context import TurnLimits
from src.repositories.group_repository import GroupRepository
from src.repositories.issue_repository import IssueRepository
from src.repositories.moderation_repository import ModerationRepository
from src.repositories.permission_repository import PermissionRepository
from src.repositories.report_repository import ReportRepository
from src.repositories.flower_repository import FlowerRepository
from src.repositories.refresh_token_repository import RefreshTokenRepository
from src.repositories.tag_follow_repository import TagFollowRepository
from src.infra.minio_client import MinioStorage
from src.repositories.user_repository import UserRepository
from src.services.issue_service import IssueService
from src.services.moderation_service import ModerationService
from src.services.permission_service import PermissionService
from src.services.authz import AuthorizationService
from src.services.group_service import GroupService
from src.services.report_service import ReportService
from src.services.flower_service import FlowerService
from src.services.refresh_token_service import RefreshTokenService
from src.services.tag_service import TagService


class _PresignFake:
    """Minimal stand-in for ``MinioStorage`` in unit tests.

    Only exposes the surface routers touch: ``presigned_get_url``. The
    returned URL is deterministic so contract tests can assert against
    it (e.g., "the GET /reports response replaced /uploads/<key> with
    https://test-presigned/<key>?sig=stub").
    """

    @staticmethod
    def presigned_get_url(key: str) -> str:
        return f"https://test-presigned/{key}?sig=stub"


class InMemoryProvider(Provider):
    """Wraps the ``services`` dict from conftest.py into dishka providers.

    Usage::

        provider = InMemoryProvider(services_dict)
        container = make_async_container(provider)
    """

    def __init__(self, services: dict) -> None:
        super().__init__()
        self._svc = services

    @provide(scope=Scope.REQUEST)
    def user_repo(self) -> UserRepository:
        return self._svc["user_repo"]

    @provide(scope=Scope.REQUEST)
    def group_repo(self) -> GroupRepository:
        return self._svc["group_repo"]

    @provide(scope=Scope.REQUEST)
    def report_repo(self) -> ReportRepository:
        return self._svc["report_repo"]

    @provide(scope=Scope.REQUEST)
    def permission_repo(self) -> PermissionRepository:
        return self._svc["permission_repo"]

    @provide(scope=Scope.REQUEST)
    def issue_repo(self) -> IssueRepository:
        return self._svc["issue_repo"]

    @provide(scope=Scope.REQUEST)
    def moderation_repo(self) -> ModerationRepository:
        return self._svc["mod_repo"]

    @provide(scope=Scope.REQUEST)
    def assist_repo(self) -> AssistRepository:
        return self._svc.get("assist_repo", InMemoryAssistRepository())

    @provide(scope=Scope.REQUEST)
    def minio_storage(self) -> MinioStorage:
        # Test stub if conftest didn't provide one. Tests that exercise
        # upload URL rewriting put a real ``MinioStorage`` (or a custom
        # fake with `presigned_get_url`) into the services dict; the
        # rest get this deterministic stub so endpoints inject cleanly.
        return self._svc.get("minio_storage", _PresignFake())

    @provide(scope=Scope.REQUEST)
    def permission_service(self) -> PermissionService:
        return self._svc["perm_svc"]

    @provide(scope=Scope.REQUEST)
    def authz_service(self) -> AuthorizationService:
        return self._svc["authz_svc"]

    @provide(scope=Scope.REQUEST)
    def group_service(self) -> GroupService:
        return self._svc["group_svc"]

    @provide(scope=Scope.REQUEST)
    def report_service(self) -> ReportService:
        return self._svc["report_svc"]

    @provide(scope=Scope.REQUEST)
    def issue_service(self) -> IssueService:
        return self._svc["issue_svc"]

    @provide(scope=Scope.REQUEST)
    def moderation_service(self) -> ModerationService:
        return self._svc["mod_svc"]

    @provide(scope=Scope.REQUEST)
    def tag_follow_repo(self) -> TagFollowRepository:
        return self._svc["tag_follow_repo"]

    @provide(scope=Scope.REQUEST)
    def flower_repo(self) -> FlowerRepository:
        return self._svc["flower_repo"]

    @provide(scope=Scope.REQUEST)
    def refresh_token_repo(self) -> RefreshTokenRepository:
        return self._svc["refresh_token_repo"]

    @provide(scope=Scope.REQUEST)
    def refresh_token_service(self) -> RefreshTokenService:
        return self._svc["refresh_token_svc"]

    @provide(scope=Scope.REQUEST)
    def tag_service(self) -> TagService:
        return self._svc["tag_svc"]

    @provide(scope=Scope.REQUEST)
    def flower_service(self) -> FlowerService:
        return self._svc["flower_svc"]

    @provide(scope=Scope.APP)
    def proxy_client(self) -> ClaudeProxyClient:
        return ClaudeProxyClient(url="http://localhost:9999")

    @provide(scope=Scope.REQUEST)
    def assistant_service(
        self, repo: AssistRepository, proxy: ClaudeProxyClient,
    ) -> AssistantService:
        # If a pre-built service was provided (e.g. with a fake proxy),
        # use it instead of creating one.
        if "assistant_service" in self._svc:
            return self._svc["assistant_service"]
        return AssistantService(
            repo=repo,
            proxy_client=proxy,
            base_system_prompt="Test assistant.",
            turn_limits=TurnLimits(max_turns=10, max_chars=5000),
            context_char_budget=3000,
        )


def make_test_container(services: dict):
    """Build a dishka container backed by in-memory repos."""
    return make_async_container(InMemoryProvider(services))
