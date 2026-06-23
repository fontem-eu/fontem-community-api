from __future__ import annotations

from abc import ABC, abstractmethod

from src.domain.resource_grant import ResourceGrant


class ResourceGrantRepository(ABC):
    @abstractmethod
    async def set_grant(self, resource_type: str, resource_id: str, user_id: str, level: str) -> None: ...

    @abstractmethod
    async def remove_grant(self, resource_type: str, resource_id: str, user_id: str) -> None: ...

    @abstractmethod
    async def get_level(self, resource_type: str, resource_id: str, user_id: str) -> str | None: ...

    @abstractmethod
    async def list_grants(self, resource_type: str, resource_id: str) -> list[ResourceGrant]: ...
