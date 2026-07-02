"""Data Studio endpoints — owner-private data projects with queries and plots.

Thin handlers delegating to :class:`DataProjectService`. Every route requires
auth; the service enforces ``created_by == user`` ownership. Only recipes are
stored — query execution + the DuckDB combine happen client-side.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Annotated

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from src.api.auth import get_current_user
from src.api.openapi_responses import RESOURCE_RESPONSES, UuidPath
from src.domain.user import User
from src.services.data_project_service import DataProjectService

router = APIRouter(prefix="/studio", tags=["studio"], responses=RESOURCE_RESPONSES)


class CreateProjectRequest(BaseModel):
    name: str = Field(default="", max_length=300)


class RenameProjectRequest(BaseModel):
    name: str = Field(min_length=1, max_length=300)


class CreateQueryRequest(BaseModel):
    name: str = Field(default="", max_length=300)
    lang: str = Field(default="cypher", max_length=20)
    query: str = Field(default="", max_length=8000)


class UpdateQueryRequest(BaseModel):
    name: str | None = Field(default=None, max_length=300)
    lang: str | None = Field(default=None, max_length=20)
    query: str | None = Field(default=None, max_length=8000)


class CreatePlotRequest(BaseModel):
    name: str = Field(default="", max_length=300)
    spec: dict = Field(default_factory=dict)


class UpdatePlotRequest(BaseModel):
    name: str | None = Field(default=None, max_length=300)
    spec: dict | None = None


# ── projects ────────────────────────────────────────────────────
@router.get("/projects")
@inject
async def list_projects(
    *,
    svc: FromDishka[DataProjectService],
    user: Annotated[User, Depends(get_current_user)],
) -> list[dict]:
    return [asdict(p) for p in await svc.list_projects(user.id)]


@router.post("/projects", status_code=201)
@inject
async def create_project(
    body: CreateProjectRequest,
    *,
    svc: FromDishka[DataProjectService],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    return asdict(await svc.create_project(user.id, body.name))


@router.get("/projects/{project_id}")
@inject
async def get_project(
    project_id: UuidPath,
    *,
    svc: FromDishka[DataProjectService],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    return asdict(await svc.get_project(user.id, project_id))


@router.put("/projects/{project_id}")
@inject
async def rename_project(
    project_id: UuidPath,
    body: RenameProjectRequest,
    *,
    svc: FromDishka[DataProjectService],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    return asdict(await svc.rename_project(user.id, project_id, body.name))


@router.delete("/projects/{project_id}", status_code=204)
@inject
async def delete_project(
    project_id: UuidPath,
    *,
    svc: FromDishka[DataProjectService],
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    await svc.delete_project(user.id, project_id)


# ── queries ─────────────────────────────────────────────────────
@router.post("/projects/{project_id}/queries", status_code=201)
@inject
async def create_query(
    project_id: UuidPath,
    body: CreateQueryRequest,
    *,
    svc: FromDishka[DataProjectService],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    return asdict(await svc.add_query(user.id, project_id, body.name, body.lang, body.query))


@router.put("/projects/{project_id}/queries/{query_id}")
@inject
async def update_query(
    project_id: UuidPath,
    query_id: UuidPath,
    body: UpdateQueryRequest,
    *,
    svc: FromDishka[DataProjectService],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    return asdict(await svc.update_query(user.id, project_id, query_id, body.name, body.lang, body.query))


@router.delete("/projects/{project_id}/queries/{query_id}", status_code=204)
@inject
async def delete_query(
    project_id: UuidPath,
    query_id: UuidPath,
    *,
    svc: FromDishka[DataProjectService],
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    await svc.delete_query(user.id, project_id, query_id)


@router.post("/projects/{project_id}/queries/{query_id}/duplicate", status_code=201)
@inject
async def duplicate_query(
    project_id: UuidPath,
    query_id: UuidPath,
    *,
    svc: FromDishka[DataProjectService],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    return asdict(await svc.duplicate_query(user.id, project_id, query_id))


# ── plots ───────────────────────────────────────────────────────
@router.post("/projects/{project_id}/plots", status_code=201)
@inject
async def create_plot(
    project_id: UuidPath,
    body: CreatePlotRequest,
    *,
    svc: FromDishka[DataProjectService],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    return asdict(await svc.add_plot(user.id, project_id, body.name, body.spec))


@router.put("/projects/{project_id}/plots/{plot_id}")
@inject
async def update_plot(
    project_id: UuidPath,
    plot_id: UuidPath,
    body: UpdatePlotRequest,
    *,
    svc: FromDishka[DataProjectService],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    return asdict(await svc.update_plot(user.id, project_id, plot_id, body.name, body.spec))


@router.delete("/projects/{project_id}/plots/{plot_id}", status_code=204)
@inject
async def delete_plot(
    project_id: UuidPath,
    plot_id: UuidPath,
    *,
    svc: FromDishka[DataProjectService],
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    await svc.delete_plot(user.id, project_id, plot_id)
