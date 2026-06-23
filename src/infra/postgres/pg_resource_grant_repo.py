from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.resource_grant import ResourceGrant
from src.infra.postgres.models import ResourceGrantModel
from src.repositories.resource_grant_repository import ResourceGrantRepository


class PgResourceGrantRepository(ResourceGrantRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def set_grant(self, resource_type: str, resource_id: str, user_id: str, level: str) -> None:
        existing = await self._row(resource_type, resource_id, user_id)
        if existing is None:
            self._session.add(ResourceGrantModel(
                resource_type=resource_type, resource_id=resource_id,
                user_id=user_id, level=level,
                created_at=datetime.now(timezone.utc),
            ))
        else:
            existing.level = level
        await self._session.commit()

    async def _row(self, resource_type: str, resource_id: str, user_id: str) -> ResourceGrantModel | None:
        result = await self._session.execute(
            select(ResourceGrantModel)
            .where(ResourceGrantModel.resource_type == resource_type)
            .where(ResourceGrantModel.resource_id == resource_id)
            .where(ResourceGrantModel.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def remove_grant(self, resource_type: str, resource_id: str, user_id: str) -> None:
        await self._session.execute(
            delete(ResourceGrantModel)
            .where(ResourceGrantModel.resource_type == resource_type)
            .where(ResourceGrantModel.resource_id == resource_id)
            .where(ResourceGrantModel.user_id == user_id)
        )
        await self._session.commit()

    async def get_level(self, resource_type: str, resource_id: str, user_id: str) -> str | None:
        row = await self._row(resource_type, resource_id, user_id)
        return row.level if row is not None else None

    async def list_grants(self, resource_type: str, resource_id: str) -> list[ResourceGrant]:
        result = await self._session.execute(
            select(ResourceGrantModel)
            .where(ResourceGrantModel.resource_type == resource_type)
            .where(ResourceGrantModel.resource_id == resource_id)
        )
        return [
            ResourceGrant(
                resource_type=r.resource_type, resource_id=r.resource_id,
                user_id=r.user_id, level=r.level, created_at=r.created_at,
            )
            for r in result.scalars().all()
        ]
