"""PostgreSQL repository for Data Studio projects, queries and plots."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.data_project import DataPlot, DataProject, DataQuery
from src.infra.postgres.models import DataPlotModel, DataProjectModel, DataQueryModel
from src.repositories.data_project_repository import DataProjectRepository


class PgDataProjectRepository(DataProjectRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _query_to_domain(row: DataQueryModel) -> DataQuery:
        return DataQuery(
            id=row.id, project_id=row.project_id, name=row.name, lang=row.lang,
            query=row.query, sort_order=row.sort_order,
            created_at=row.created_at, updated_at=row.updated_at,
        )

    @staticmethod
    def _plot_to_domain(row: DataPlotModel) -> DataPlot:
        return DataPlot(
            id=row.id, project_id=row.project_id, name=row.name, spec=dict(row.spec or {}),
            sort_order=row.sort_order, created_at=row.created_at, updated_at=row.updated_at,
        )

    def _project_to_domain(self, row: DataProjectModel) -> DataProject:
        return DataProject(
            id=row.id, name=row.name, created_by=row.created_by,
            created_at=row.created_at, updated_at=row.updated_at,
            queries=[self._query_to_domain(q) for q in row.queries],
            plots=[self._plot_to_domain(p) for p in row.plots],
        )

    async def create_project(self, project: DataProject) -> DataProject:
        now = datetime.now(timezone.utc)
        model = DataProjectModel(
            id=project.id or str(uuid4()), name=project.name,
            created_by=project.created_by, created_at=now, updated_at=now,
        )
        self._session.add(model)
        await self._session.commit()
        return DataProject(
            id=model.id, name=model.name, created_by=model.created_by,
            created_at=model.created_at, updated_at=model.updated_at, queries=[], plots=[],
        )

    async def get_project(self, project_id: str) -> DataProject | None:
        stmt = select(DataProjectModel).where(DataProjectModel.id == project_id)
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return self._project_to_domain(row) if row else None

    async def list_for_user(self, user_id: str) -> list[DataProject]:
        stmt = (
            select(DataProjectModel)
            .where(DataProjectModel.created_by == user_id)
            .order_by(DataProjectModel.updated_at.desc())
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [self._project_to_domain(r) for r in rows]

    async def update_project(self, project: DataProject) -> DataProject:
        model = await self._session.get(DataProjectModel, project.id)
        model.name = project.name
        await self._session.commit()
        return await self.get_project(project.id)

    async def delete_project(self, project_id: str) -> None:
        model = await self._session.get(DataProjectModel, project_id)
        if model is not None:
            await self._session.delete(model)
            await self._session.commit()

    async def add_query(self, query: DataQuery) -> DataQuery:
        now = datetime.now(timezone.utc)
        model = DataQueryModel(
            id=query.id or str(uuid4()), project_id=query.project_id, name=query.name,
            lang=query.lang, query=query.query, sort_order=query.sort_order,
            created_at=now, updated_at=now,
        )
        self._session.add(model)
        await self._session.commit()
        return self._query_to_domain(model)

    async def update_query(self, query: DataQuery) -> DataQuery:
        model = await self._session.get(DataQueryModel, query.id)
        model.name = query.name
        model.lang = query.lang
        model.query = query.query
        await self._session.commit()
        return self._query_to_domain(model)

    async def delete_query(self, query_id: str) -> None:
        model = await self._session.get(DataQueryModel, query_id)
        if model is not None:
            await self._session.delete(model)
            await self._session.commit()

    async def add_plot(self, plot: DataPlot) -> DataPlot:
        now = datetime.now(timezone.utc)
        model = DataPlotModel(
            id=plot.id or str(uuid4()), project_id=plot.project_id, name=plot.name,
            spec=plot.spec, sort_order=plot.sort_order, created_at=now, updated_at=now,
        )
        self._session.add(model)
        await self._session.commit()
        return self._plot_to_domain(model)

    async def update_plot(self, plot: DataPlot) -> DataPlot:
        model = await self._session.get(DataPlotModel, plot.id)
        model.name = plot.name
        model.spec = plot.spec
        await self._session.commit()
        return self._plot_to_domain(model)

    async def delete_plot(self, plot_id: str) -> None:
        model = await self._session.get(DataPlotModel, plot_id)
        if model is not None:
            await self._session.delete(model)
            await self._session.commit()
