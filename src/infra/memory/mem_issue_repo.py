from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4

from src.domain.issue import Comment, Issue
from src.repositories.issue_repository import IssueRepository


class InMemoryIssueRepository(IssueRepository):
    def __init__(self) -> None:
        self._issues: dict[str, Issue] = {}
        self._comments: list[Comment] = []
        self._votes: dict[str, dict[str, str]] = {}  # issue_id -> {user_id: direction}

    async def create(self, issue: Issue) -> Issue:
        if issue.id is None:
            issue.id = str(uuid4())
        now = datetime.now(timezone.utc)
        issue.created_at = issue.created_at or now
        issue.updated_at = issue.updated_at or now
        self._issues[issue.id] = deepcopy(issue)
        return deepcopy(issue)

    async def get_by_id(self, issue_id: str) -> Issue | None:
        issue = self._issues.get(issue_id)
        return deepcopy(issue) if issue else None

    async def update_status(self, issue_id: str, status: str) -> None:
        issue = self._issues.get(issue_id)
        if issue is not None:
            issue.status = status
            issue.updated_at = datetime.now(timezone.utc)

    async def list_for_entity(
        self, entity_type: str, entity_id: str, limit: int, offset: int
    ) -> list[Issue]:
        results = [
            deepcopy(i)
            for i in self._issues.values()
            if i.entity_type == entity_type and i.entity_id == entity_id
        ]
        results.sort(key=lambda i: i.created_at or datetime.min, reverse=True)
        return results[offset : offset + limit]

    async def list_open(self, limit: int, offset: int) -> list[Issue]:
        results = [deepcopy(i) for i in self._issues.values() if i.status == "open"]
        results.sort(key=lambda i: i.created_at or datetime.min, reverse=True)
        return results[offset : offset + limit]

    async def add_comment(self, comment: Comment) -> Comment:
        if comment.id is None:
            comment.id = str(uuid4())
        comment.created_at = comment.created_at or datetime.now(timezone.utc)
        self._comments.append(deepcopy(comment))
        return deepcopy(comment)

    async def get_comments(self, parent_type: str, parent_id: str) -> list[Comment]:
        results = [
            deepcopy(c)
            for c in self._comments
            if c.parent_type == parent_type and c.parent_id == parent_id
        ]
        results.sort(key=lambda c: c.created_at or datetime.min)
        return results

    async def vote(self, issue_id: str, user_id: str, direction: str) -> None:
        self._votes.setdefault(issue_id, {})[user_id] = direction

    async def get_vote_count(self, issue_id: str) -> int:
        votes = self._votes.get(issue_id, {})
        count = 0
        for direction in votes.values():
            if direction == "up":
                count += 1
            elif direction == "down":
                count -= 1
        return count
