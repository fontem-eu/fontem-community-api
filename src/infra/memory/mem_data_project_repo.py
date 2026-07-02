"""In-memory Data Studio repository for unit tests (0 I/O)."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4

from src.domain.data_project import DataPlot, DataProject, DataQuery
from src.repositories.data_project_repository import DataProjectRepository


def _now() -> datetime:
    return datetime.now(timezone.utc)


class InMemoryDataProjectRepository(DataProjectRepository):
    def __init__(self) -> None:
        self._projects: dict[str, DataProject] = {}
        self._queries: dict[str, DataQuery] = {}
        self._plots: dict[str, DataPlot] = {}

    def _hydrate(self, project_id: str) -> DataProject:
        p = deepcopy(self._projects[project_id])
        p.queries = sorted(
            (deepcopy(q) for q in self._queries.values() if q.project_id == project_id),
            key=lambda q: q.sort_order,
        )
        p.plots = sorted(
            (deepcopy(pl) for pl in self._plots.values() if pl.project_id == project_id),
            key=lambda pl: pl.sort_order,
        )
        return p

    async def create_project(self, project: DataProject) -> DataProject:
        project.id = project.id or str(uuid4())
        project.created_at = project.created_at or _now()
        project.updated_at = project.updated_at or _now()
        project.queries, project.plots = [], []
        self._projects[project.id] = deepcopy(project)
        return self._hydrate(project.id)

    async def get_project(self, project_id: str) -> DataProject | None:
        return self._hydrate(project_id) if project_id in self._projects else None

    async def list_for_user(self, user_id: str) -> list[DataProject]:
        ids = [pid for pid, p in self._projects.items() if p.created_by == user_id]
        projects = [self._hydrate(pid) for pid in ids]
        return sorted(projects, key=lambda p: p.updated_at or _now(), reverse=True)

    async def update_project(self, project: DataProject) -> DataProject:
        stored = self._projects[project.id]
        stored.name = project.name
        stored.updated_at = _now()
        return self._hydrate(project.id)

    async def delete_project(self, project_id: str) -> None:
        self._projects.pop(project_id, None)
        for qid in [q.id for q in self._queries.values() if q.project_id == project_id]:
            self._queries.pop(qid, None)
        for pid in [p.id for p in self._plots.values() if p.project_id == project_id]:
            self._plots.pop(pid, None)

    async def add_query(self, query: DataQuery) -> DataQuery:
        query.id = query.id or str(uuid4())
        query.created_at = _now()
        query.updated_at = _now()
        self._queries[query.id] = deepcopy(query)
        return deepcopy(query)

    async def update_query(self, query: DataQuery) -> DataQuery:
        stored = self._queries[query.id]
        stored.name, stored.lang, stored.query = query.name, query.lang, query.query
        stored.updated_at = _now()
        return deepcopy(stored)

    async def delete_query(self, query_id: str) -> None:
        self._queries.pop(query_id, None)

    async def add_plot(self, plot: DataPlot) -> DataPlot:
        plot.id = plot.id or str(uuid4())
        plot.created_at = _now()
        plot.updated_at = _now()
        self._plots[plot.id] = deepcopy(plot)
        return deepcopy(plot)

    async def update_plot(self, plot: DataPlot) -> DataPlot:
        stored = self._plots[plot.id]
        stored.name, stored.spec = plot.name, plot.spec
        stored.updated_at = _now()
        return deepcopy(stored)

    async def delete_plot(self, plot_id: str) -> None:
        self._plots.pop(plot_id, None)
