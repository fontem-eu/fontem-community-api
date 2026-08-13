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
            actor_kind=row.actor_kind,
            conversation_id=row.conversation_id,
            message_id=row.message_id,
            request_id=row.request_id,
        )

    async def record(self, event: ActivityEvent) -> ActivityEvent:
        model = ActivityLogModel(
            actor_id=event.actor_id,
            entity_type=event.entity_type,
            entity_id=event.entity_id,
            action=event.action,
            summary=event.summary,
            actor_kind=event.actor_kind,
            conversation_id=event.conversation_id,
            message_id=event.message_id,
            request_id=event.request_id,
        )
        self._session.add(model)
        # Flush, not commit. The audit row belongs to the same transaction as
        # the change it describes: either both land or neither does, and a
        # record of something that was rolled back is worse than no record.
        #
        # Committing here also committed whatever else the request had
        # pending, which made an audit write a surprising place for unrelated
        # half-finished work to become permanent. The session provider
        # commits at the end of the request.
        await self._session.flush()
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
