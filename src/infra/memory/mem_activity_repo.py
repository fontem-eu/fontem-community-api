from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4

from src.domain.activity import ActivityEvent
from src.repositories.activity_repository import ActivityRepository


class InMemoryActivityRepository(ActivityRepository):
    def __init__(self) -> None:
        self._events: list[ActivityEvent] = []

    async def record(self, event: ActivityEvent) -> ActivityEvent:
        if event.id is None:
            event.id = str(uuid4())
        event.created_at = event.created_at or datetime.now(timezone.utc)
        self._events.append(deepcopy(event))
        return deepcopy(event)

    async def list_for_actor(self, actor_id: str, limit: int, offset: int) -> list[ActivityEvent]:
        rows = [deepcopy(e) for e in self._events if e.actor_id == actor_id]
        rows.sort(
            key=lambda e: e.created_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        return rows[offset : offset + limit]
