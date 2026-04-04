from __future__ import annotations

from abc import ABC, abstractmethod

from src.domain.group import Group


class GroupRepository(ABC):
    @abstractmethod
    async def create(self, group: Group) -> Group: ...

    @abstractmethod
    async def get_by_id(self, group_id: str) -> Group | None: ...

    @abstractmethod
    async def add_member(self, group_id: str, user_id: str) -> None: ...

    @abstractmethod
    async def remove_member(self, group_id: str, user_id: str) -> None: ...

    @abstractmethod
    async def get_members(self, group_id: str) -> list[str]: ...

    @abstractmethod
    async def get_user_groups(self, user_id: str) -> list[Group]: ...
