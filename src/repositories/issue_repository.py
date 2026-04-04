from __future__ import annotations

from abc import ABC, abstractmethod

from src.domain.issue import Comment, Issue


class IssueRepository(ABC):
    @abstractmethod
    async def create(self, issue: Issue) -> Issue: ...

    @abstractmethod
    async def get_by_id(self, issue_id: str) -> Issue | None: ...

    @abstractmethod
    async def update_status(self, issue_id: str, status: str) -> None: ...

    @abstractmethod
    async def list_for_entity(self, entity_type: str, entity_id: str, limit: int, offset: int) -> list[Issue]: ...

    @abstractmethod
    async def list_open(self, limit: int, offset: int) -> list[Issue]: ...

    @abstractmethod
    async def add_comment(self, comment: Comment) -> Comment: ...

    @abstractmethod
    async def get_comments(self, parent_type: str, parent_id: str) -> list[Comment]: ...

    @abstractmethod
    async def vote(self, issue_id: str, user_id: str, direction: str) -> None: ...

    @abstractmethod
    async def get_vote_count(self, issue_id: str) -> int: ...
