from __future__ import annotations

from abc import ABC, abstractmethod

from src.domain.activity import ActivityEvent


class ActivityRepository(ABC):
    @abstractmethod
    async def record(self, event: ActivityEvent) -> ActivityEvent: ...

    @abstractmethod
    async def list_for_actor(self, actor_id: str, limit: int, offset: int) -> list[ActivityEvent]: ...
