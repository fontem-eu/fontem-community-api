"""Abstract repository for the feed-query catalogue."""
from __future__ import annotations

from abc import ABC, abstractmethod

from src.domain.named_query import NamedQuery, QueryGroup


class NamedQueryRepository(ABC):
    # ── named queries ────────────────────────────────────────
    @abstractmethod
    async def create_query(self, query: NamedQuery) -> NamedQuery: ...

    @abstractmethod
    async def get_query(self, query_id: str) -> NamedQuery | None: ...

    @abstractmethod
    async def get_query_by_slug(self, slug: str) -> NamedQuery | None: ...

    @abstractmethod
    async def list_queries(self, status: str | None = None) -> list[NamedQuery]: ...

    @abstractmethod
    async def update_query(self, query: NamedQuery) -> NamedQuery: ...

    @abstractmethod
    async def delete_query(self, query_id: str) -> None: ...

    # ── groups ───────────────────────────────────────────────
    @abstractmethod
    async def create_group(self, group: QueryGroup) -> QueryGroup: ...

    @abstractmethod
    async def get_group(self, group_id: str) -> QueryGroup | None: ...

    @abstractmethod
    async def get_group_by_slug(self, slug: str) -> QueryGroup | None: ...

    @abstractmethod
    async def list_groups(self, visibility: str | None = None) -> list[QueryGroup]: ...

    @abstractmethod
    async def update_group(self, group: QueryGroup) -> QueryGroup: ...

    @abstractmethod
    async def delete_group(self, group_id: str) -> None: ...

    # ── membership (many-to-many) ────────────────────────────
    @abstractmethod
    async def set_group_queries(self, group_id: str, query_ids: list[str]) -> None:
        """Replace the group's membership with ``query_ids``, in that order.

        Replace-the-whole-set rather than add/remove: the admin UI edits an
        ordered list, and a positional edit expressed as a diff is where
        ordering bugs live.
        """

    @abstractmethod
    async def groups_for_query(self, query_id: str) -> list[QueryGroup]: ...
