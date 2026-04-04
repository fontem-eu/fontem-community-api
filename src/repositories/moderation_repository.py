from __future__ import annotations

from abc import ABC, abstractmethod

from src.domain.moderation import Flag, Sanction


class ModerationRepository(ABC):
    @abstractmethod
    async def add_flag(self, flag: Flag) -> Flag: ...

    @abstractmethod
    async def count_flags(self, target_type: str, target_id: str) -> int: ...

    @abstractmethod
    async def has_flagged(self, target_type: str, target_id: str, user_id: str) -> bool: ...

    @abstractmethod
    async def list_flagged(self, limit: int, offset: int) -> list[Flag]: ...

    @abstractmethod
    async def resolve_flags(self, target_type: str, target_id: str, action: str, moderator_id: str) -> None: ...

    @abstractmethod
    async def add_sanction(self, sanction: Sanction) -> Sanction: ...

    @abstractmethod
    async def get_active_sanction(self, user_id: str) -> Sanction | None: ...

    @abstractmethod
    async def lift_sanction(self, sanction_id: str) -> None: ...

    @abstractmethod
    async def get_log(self, limit: int, offset: int) -> list[dict]: ...
