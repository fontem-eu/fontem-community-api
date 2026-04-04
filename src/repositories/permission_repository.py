from __future__ import annotations

from abc import ABC, abstractmethod

from src.domain.report import AccessGrant


class PermissionRepository(ABC):
    @abstractmethod
    async def get_report_access(self, user_id: str, report_id: str) -> str | None: ...

    @abstractmethod
    async def get_report_visibility(self, report_id: str) -> str | None: ...

    @abstractmethod
    async def set_user_access(self, report_id: str, user_id: str, level: str) -> None: ...

    @abstractmethod
    async def set_group_access(self, report_id: str, group_id: str, level: str) -> None: ...

    @abstractmethod
    async def remove_user_access(self, report_id: str, user_id: str) -> None: ...

    @abstractmethod
    async def remove_group_access(self, report_id: str, group_id: str) -> None: ...

    @abstractmethod
    async def list_collaborators(self, report_id: str) -> list[AccessGrant]: ...
