"""Issue + comment service — community feedback channel.

Routes every policy decision through :class:`AuthorizationService`
(create / comment / vote / resolve) so the audit log captures every
attempt and the rules sit in one place. Pre-authz validation kept
deliberately small: only the "user exists" guard, because the test
contract surfaces ``PermissionDenied("User not found")`` and we want
that distinct from a generic "unauthenticated" deny.
"""
from __future__ import annotations

from src.domain.issue import Comment, Issue
from src.repositories.issue_repository import IssueRepository
from src.repositories.user_repository import UserRepository
from src.services.authz import Action, AuthorizationService, ResourceRef
from src.services.activity_service import ActivityService
from src.services.exceptions import Conflict, NotFound, PermissionDenied


class IssueService:
    def __init__(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        issues: IssueRepository,
        users: UserRepository,
        authz: AuthorizationService,
        activity: ActivityService,
    ) -> None:
        self._issues = issues
        self._activity = activity
        self._users = users
        self._authz = authz

    # pylint: disable-next=too-many-arguments,too-many-positional-arguments
    async def create(
        self,
        user_id: str,
        title: str,
        body: str,
        issue_type: str,
        entity_type: str,
        entity_id: str,
    ) -> Issue:
        user = await self._users.get_by_id(user_id)
        if user is None:
            raise PermissionDenied("User not found")
        principal = await self._authz.principal(user_id)
        await self._authz.require(principal, Action.ISSUES_CREATE)
        issue = Issue(
            title=title,
            body_md=body,
            issue_type=issue_type,
            entity_type=entity_type,
            entity_id=entity_id,
            created_by=user_id,
        )
        created = await self._issues.create(issue)
        await self._activity.record(user_id, "issue", created.id or "", "created", created.title)
        return created

    async def add_comment(self, user_id: str, issue_id: str, body: str) -> Comment:
        issue = await self._issues.get_by_id(issue_id)
        if issue is None:
            raise NotFound(f"Issue {issue_id} not found")
        if issue.status in ("closed", "resolved", "rejected"):
            raise Conflict(f"Issue {issue_id} is {issue.status} — no new comments")
        principal = await self._authz.principal(user_id)
        await self._authz.require(
            principal, Action.ISSUES_COMMENT, ResourceRef.for_issue(issue),
        )
        comment = Comment(
            parent_type="issue",
            parent_id=issue_id,
            body_md=body,
            author_id=user_id,
        )
        return await self._issues.add_comment(comment)

    async def vote(self, user_id: str, issue_id: str, direction: str) -> None:
        issue = await self._issues.get_by_id(issue_id)
        if issue is None:
            raise NotFound(f"Issue {issue_id} not found")
        principal = await self._authz.principal(user_id)
        await self._authz.require(
            principal, Action.ISSUES_VOTE, ResourceRef.for_issue(issue),
        )
        await self._issues.vote(issue_id, user_id, direction)

    async def resolve(self, moderator_id: str, issue_id: str, status: str) -> None:
        user = await self._users.get_by_id(moderator_id)
        if user is None:
            raise PermissionDenied("Moderator role required")
        issue = await self._issues.get_by_id(issue_id)
        if issue is None:
            raise NotFound(f"Issue {issue_id} not found")
        principal = await self._authz.principal(moderator_id)
        try:
            await self._authz.require(
                principal, Action.ISSUES_SET_STATUS, ResourceRef.for_issue(issue),
            )
        except PermissionDenied as e:
            # Preserve the legacy 403 message ("Moderator role required")
            # the existing tests + UI rely on, while keeping the audit
            # trail intact (the require call has already recorded the
            # deny).
            raise PermissionDenied("Moderator role required") from e
        await self._issues.update_status(issue_id, status)

    async def list_for_entity(
        self, entity_type: str, entity_id: str, limit: int, offset: int
    ) -> list[Issue]:
        return await self._issues.list_for_entity(entity_type, entity_id, limit, offset)

    async def list_open(self, limit: int, offset: int) -> list[Issue]:
        return await self._issues.list_open(limit, offset)
