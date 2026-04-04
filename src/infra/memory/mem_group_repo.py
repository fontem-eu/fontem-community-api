from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4

from src.domain.group import Group
from src.repositories.group_repository import GroupRepository


class InMemoryGroupRepository(GroupRepository):
    def __init__(self) -> None:
        self._groups: dict[str, Group] = {}
        self._members: dict[str, set[str]] = {}  # group_id -> set of user_ids

    async def create(self, group: Group) -> Group:
        if group.id is None:
            group.id = str(uuid4())
        group.created_at = group.created_at or datetime.now(timezone.utc)
        self._groups[group.id] = deepcopy(group)
        self._members.setdefault(group.id, set())
        return deepcopy(group)

    async def get_by_id(self, group_id: str) -> Group | None:
        group = self._groups.get(group_id)
        return deepcopy(group) if group else None

    async def add_member(self, group_id: str, user_id: str) -> None:
        self._members.setdefault(group_id, set()).add(user_id)

    async def remove_member(self, group_id: str, user_id: str) -> None:
        members = self._members.get(group_id)
        if members is not None:
            members.discard(user_id)

    async def get_members(self, group_id: str) -> list[str]:
        return list(self._members.get(group_id, set()))

    async def get_user_groups(self, user_id: str) -> list[Group]:
        result: list[Group] = []
        for group_id, members in self._members.items():
            if user_id in members:
                group = self._groups.get(group_id)
                if group is not None:
                    result.append(deepcopy(group))
        return result
