from __future__ import annotations

from src.domain.issue import Comment, Issue
from src.repositories.issue_repository import IssueRepository
from src.repositories.user_repository import UserRepository
from src.services.exceptions import Conflict, NotFound, PermissionDenied

TRUST_LEVELS = ["new_user", "commenter", "contributor", "moderator", "admin"]


class IssueService:
    def __init__(self, issues: IssueRepository, users: UserRepository) -> None:
        self._issues = issues
        self._users = users

    def _trust_rank(self, level: str) -> int:
        try:
            return TRUST_LEVELS.index(level)
        except ValueError:
            return 0

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
        if self._trust_rank(user.trust_level) < self._trust_rank("contributor"):
            raise PermissionDenied("Trust level must be at least 'contributor' to create issues")
        issue = Issue(
            title=title,
            body_md=body,
            issue_type=issue_type,
            entity_type=entity_type,
            entity_id=entity_id,
            created_by=user_id,
        )
        return await self._issues.create(issue)

    async def add_comment(self, user_id: str, issue_id: str, body: str) -> Comment:
        # Check not muted/suspended
        sanction = await self._users.get_active_sanction(user_id)
        if sanction is not None and sanction.type in ("mute", "suspend"):
            raise PermissionDenied(f"User is currently {sanction.type}d")
        issue = await self._issues.get_by_id(issue_id)
        if issue is None:
            raise NotFound(f"Issue {issue_id} not found")
        if issue.status in ("closed", "resolved", "rejected"):
            raise Conflict(f"Issue {issue_id} is {issue.status} — no new comments")
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
        await self._issues.vote(issue_id, user_id, direction)

    async def resolve(self, moderator_id: str, issue_id: str, status: str) -> None:
        user = await self._users.get_by_id(moderator_id)
        if user is None:
            raise PermissionDenied("User not found")
        roles = await self._users.get_roles(moderator_id)
        is_mod = (
            "moderator" in roles
            or "admin" in roles
            or self._trust_rank(user.trust_level) >= self._trust_rank("moderator")
        )
        if not is_mod:
            raise PermissionDenied("Moderator role required")
        issue = await self._issues.get_by_id(issue_id)
        if issue is None:
            raise NotFound(f"Issue {issue_id} not found")
        await self._issues.update_status(issue_id, status)

    async def list_for_entity(
        self, entity_type: str, entity_id: str, limit: int, offset: int
    ) -> list[Issue]:
        return await self._issues.list_for_entity(entity_type, entity_id, limit, offset)

    async def list_open(self, limit: int, offset: int) -> list[Issue]:
        return await self._issues.list_open(limit, offset)
