"""In-memory feed-query catalogue for unit tests."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4

from src.domain.named_query import NamedQuery, QueryGroup
from src.repositories.named_query_repository import NamedQueryRepository


class InMemoryNamedQueryRepository(NamedQueryRepository):
    def __init__(self) -> None:
        self._queries: dict[str, NamedQuery] = {}
        self._groups: dict[str, QueryGroup] = {}
        # group_id -> ordered query ids
        self._members: dict[str, list[str]] = {}

    # ── named queries ────────────────────────────────────────
    async def create_query(self, query: NamedQuery) -> NamedQuery:
        now = datetime.now(timezone.utc)
        stored = deepcopy(query)
        stored.id = stored.id or str(uuid4())
        stored.created_at = now
        stored.updated_at = now
        self._queries[stored.id] = stored
        return deepcopy(stored)

    async def get_query(self, query_id: str) -> NamedQuery | None:
        found = self._queries.get(query_id)
        return deepcopy(found) if found else None

    async def get_query_by_slug(self, slug: str) -> NamedQuery | None:
        for query in self._queries.values():
            if query.slug == slug:
                return deepcopy(query)
        return None

    async def list_queries(self, status: str | None = None) -> list[NamedQuery]:
        out = [deepcopy(q) for q in self._queries.values()
               if status is None or q.status == status]
        return sorted(out, key=lambda q: q.name)

    async def update_query(self, query: NamedQuery) -> NamedQuery:
        if query.id not in self._queries:
            return query
        stored = deepcopy(query)
        stored.created_at = self._queries[query.id].created_at
        stored.updated_at = datetime.now(timezone.utc)
        self._queries[query.id] = stored
        return deepcopy(stored)

    async def delete_query(self, query_id: str) -> None:
        self._queries.pop(query_id, None)
        for members in self._members.values():
            if query_id in members:
                members.remove(query_id)

    # ── groups ───────────────────────────────────────────────
    async def create_group(self, group: QueryGroup) -> QueryGroup:
        now = datetime.now(timezone.utc)
        stored = deepcopy(group)
        stored.id = stored.id or str(uuid4())
        stored.created_at = now
        stored.updated_at = now
        stored.queries = []
        self._groups[stored.id] = stored
        self._members.setdefault(stored.id, [])
        return deepcopy(stored)

    def _hydrate(self, group: QueryGroup) -> QueryGroup:
        out = deepcopy(group)
        out.queries = [deepcopy(self._queries[qid])
                       for qid in self._members.get(group.id, [])
                       if qid in self._queries]
        return out

    async def get_group(self, group_id: str) -> QueryGroup | None:
        found = self._groups.get(group_id)
        return self._hydrate(found) if found else None

    async def get_group_by_slug(self, slug: str) -> QueryGroup | None:
        for group in self._groups.values():
            if group.slug == slug:
                return self._hydrate(group)
        return None

    async def list_groups(self, visibility: str | None = None) -> list[QueryGroup]:
        out = [self._hydrate(g) for g in self._groups.values()
               if visibility is None or g.visibility == visibility]
        return sorted(out, key=lambda g: (g.sort_order, g.name))

    async def update_group(self, group: QueryGroup) -> QueryGroup:
        if group.id not in self._groups:
            return group
        stored = deepcopy(group)
        stored.created_at = self._groups[group.id].created_at
        stored.updated_at = datetime.now(timezone.utc)
        self._groups[group.id] = stored
        return self._hydrate(stored)

    async def delete_group(self, group_id: str) -> None:
        self._groups.pop(group_id, None)
        self._members.pop(group_id, None)

    # ── membership ───────────────────────────────────────────
    async def set_group_queries(self, group_id: str, query_ids: list[str]) -> None:
        self._members[group_id] = list(query_ids)

    async def groups_for_query(self, query_id: str) -> list[QueryGroup]:
        out = [deepcopy(g) for gid, members in self._members.items()
               if query_id in members and (g := self._groups.get(gid)) is not None]
        return sorted(out, key=lambda g: (g.sort_order, g.name))
