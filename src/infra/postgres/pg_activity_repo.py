from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.activity import ActivityEvent
from src.infra.postgres.models import ActivityLogModel
from src.repositories.activity_repository import ActivityRepository


class PgActivityRepository(ActivityRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _to_domain(row: ActivityLogModel) -> ActivityEvent:
        return ActivityEvent(
            id=row.id,
            actor_id=row.actor_id,
            entity_type=row.entity_type,
            entity_id=row.entity_id,
            action=row.action,
            summary=row.summary,
            created_at=row.created_at,
        )

    async def record(self, event: ActivityEvent) -> ActivityEvent:
        model = ActivityLogModel(
            actor_id=event.actor_id,
            entity_type=event.entity_type,
            entity_id=event.entity_id,
            action=event.action,
            summary=event.summary,
        )
        self._session.add(model)
        await self._session.commit()
        return self._to_domain(model)

    async def list_for_actor(self, actor_id: str, limit: int, offset: int) -> list[ActivityEvent]:
        result = await self._session.execute(
            select(ActivityLogModel)
            .where(ActivityLogModel.actor_id == actor_id)
            .order_by(ActivityLogModel.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return [self._to_domain(r) for r in result.scalars().all()]
