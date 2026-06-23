from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.visualization import Visualization
from src.infra.postgres.models import VisualizationModel
from src.repositories.visualization_repository import VisualizationRepository


class PgVisualizationRepository(VisualizationRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _to_domain(row: VisualizationModel) -> Visualization:
        return Visualization(
            id=row.id,
            name=row.name,
            widget_type=row.widget_type,
            config=row.config or {},
            created_by=row.created_by,
            investigation_id=row.investigation_id,
            created_at=row.created_at,
        )

    async def create(self, viz: Visualization) -> Visualization:
        model = VisualizationModel(
            id=viz.id or str(uuid4()),
            name=viz.name,
            widget_type=viz.widget_type,
            config=viz.config or {},
            created_by=viz.created_by,
            investigation_id=viz.investigation_id,
            created_at=viz.created_at or datetime.now(timezone.utc),
        )
        self._session.add(model)
        await self._session.commit()
        return self._to_domain(model)

    async def get_by_id(self, viz_id: str) -> Visualization | None:
        result = await self._session.execute(
            select(VisualizationModel).where(VisualizationModel.id == viz_id)
        )
        row = result.scalar_one_or_none()
        return self._to_domain(row) if row else None

    async def update(self, viz: Visualization) -> Visualization:
        await self._session.execute(
            VisualizationModel.__table__.update()
            .where(VisualizationModel.id == viz.id)
            .values(name=viz.name, config=viz.config or {})
        )
        await self._session.commit()
        refreshed = await self.get_by_id(viz.id)  # type: ignore[arg-type]
        assert refreshed is not None
        return refreshed

    async def delete(self, viz_id: str) -> None:
        await self._session.execute(
            delete(VisualizationModel).where(VisualizationModel.id == viz_id)
        )
        await self._session.commit()

    async def list_for_user(self, user_id: str) -> list[Visualization]:
        result = await self._session.execute(
            select(VisualizationModel).where(VisualizationModel.created_by == user_id)
        )
        return [self._to_domain(r) for r in result.scalars().all()]

    async def list_by_investigation(self, investigation_id: str) -> list[Visualization]:
        result = await self._session.execute(
            select(VisualizationModel)
            .where(VisualizationModel.investigation_id == investigation_id)
        )
        return [self._to_domain(r) for r in result.scalars().all()]

    async def set_investigation(self, viz_id: str, investigation_id: str | None) -> None:
        await self._session.execute(
            VisualizationModel.__table__.update()
            .where(VisualizationModel.id == viz_id)
            .values(investigation_id=investigation_id)
        )
        await self._session.commit()
