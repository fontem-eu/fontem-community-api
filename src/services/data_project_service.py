"""Data Studio service — owner-private data projects with queries and plots.

Deliberately lightweight: projects are private to their creator, so ownership
is a simple ``created_by == user_id`` check (no sharing/authz machinery). Query
execution is NOT here — the browser runs read-only proxy queries + the DuckDB
combine; the server only persists the re-runnable recipes.
"""
from __future__ import annotations

from src.domain.data_project import DataPlot, DataProject, DataQuery
from src.repositories.data_project_repository import DataProjectRepository
from src.services.exceptions import NotFound


def _clean(name: str, fallback: str) -> str:
    return (name or "").strip()[:300] or fallback


class DataProjectService:
    def __init__(self, repo: DataProjectRepository) -> None:
        self._repo = repo

    async def _owned(self, user_id: str, project_id: str) -> DataProject:
        project = await self._repo.get_project(project_id)
        if project is None or project.created_by != user_id:
            raise NotFound(f"Data project {project_id} not found")
        return project

    @staticmethod
    def _find_query(project: DataProject, query_id: str) -> DataQuery:
        for q in project.queries:
            if q.id == query_id:
                return q
        raise NotFound(f"Query {query_id} not found")

    @staticmethod
    def _find_plot(project: DataProject, plot_id: str) -> DataPlot:
        for p in project.plots:
            if p.id == plot_id:
                return p
        raise NotFound(f"Plot {plot_id} not found")

    # ── projects ────────────────────────────────────────────────
    async def list_projects(self, user_id: str) -> list[DataProject]:
        return await self._repo.list_for_user(user_id)

    async def get_project(self, user_id: str, project_id: str) -> DataProject:
        return await self._owned(user_id, project_id)

    async def create_project(self, user_id: str, name: str) -> DataProject:
        return await self._repo.create_project(
            DataProject(name=_clean(name, "Untitled project"), created_by=user_id)
        )

    async def rename_project(self, user_id: str, project_id: str, name: str) -> DataProject:
        project = await self._owned(user_id, project_id)
        project.name = _clean(name, project.name)
        return await self._repo.update_project(project)

    async def delete_project(self, user_id: str, project_id: str) -> None:
        await self._owned(user_id, project_id)
        await self._repo.delete_project(project_id)

    # ── queries ─────────────────────────────────────────────────
    async def add_query(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self, user_id: str, project_id: str, name: str, lang: str, query: str,
    ) -> DataQuery:
        project = await self._owned(user_id, project_id)
        return await self._repo.add_query(DataQuery(
            project_id=project_id, name=_clean(name, f"Query {len(project.queries) + 1}"),
            lang=lang or "cypher", query=query or "", sort_order=len(project.queries),
        ))

    async def update_query(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self, user_id: str, project_id: str, query_id: str,
        name: str | None, lang: str | None, query: str | None,
    ) -> DataQuery:
        project = await self._owned(user_id, project_id)
        existing = self._find_query(project, query_id)
        if name is not None:
            existing.name = _clean(name, existing.name)
        if lang is not None:
            existing.lang = lang
        if query is not None:
            existing.query = query
        return await self._repo.update_query(existing)

    async def delete_query(self, user_id: str, project_id: str, query_id: str) -> None:
        project = await self._owned(user_id, project_id)
        self._find_query(project, query_id)
        await self._repo.delete_query(query_id)

    async def duplicate_query(self, user_id: str, project_id: str, query_id: str) -> DataQuery:
        project = await self._owned(user_id, project_id)
        src = self._find_query(project, query_id)
        return await self._repo.add_query(DataQuery(
            project_id=project_id, name=f"{src.name} copy", lang=src.lang, query=src.query,
            sort_order=len(project.queries),
        ))

    # ── plots ───────────────────────────────────────────────────
    async def add_plot(self, user_id: str, project_id: str, name: str, spec: dict) -> DataPlot:
        project = await self._owned(user_id, project_id)
        return await self._repo.add_plot(DataPlot(
            project_id=project_id, name=_clean(name, f"Plot {len(project.plots) + 1}"),
            spec=spec or {}, sort_order=len(project.plots),
        ))

    async def update_plot(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self, user_id: str, project_id: str, plot_id: str, name: str | None, spec: dict | None,
    ) -> DataPlot:
        project = await self._owned(user_id, project_id)
        existing = self._find_plot(project, plot_id)
        if name is not None:
            existing.name = _clean(name, existing.name)
        if spec is not None:
            existing.spec = spec
        return await self._repo.update_plot(existing)

    async def delete_plot(self, user_id: str, project_id: str, plot_id: str) -> None:
        project = await self._owned(user_id, project_id)
        self._find_plot(project, plot_id)
        await self._repo.delete_plot(plot_id)
