"""Data Studio endpoints — data projects with queries and plots.

Thin handlers delegating to :class:`DataProjectService`. Every route requires
auth; access is enforced through ``AuthorizationService`` (owner → inherited
investigation role → direct grant), and each project read carries a
``my_access`` block so the client can render read-only vs editable. Only
recipes are stored — query execution + the DuckDB combine happen client-side.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Annotated

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from src.api.auth import get_current_user
from src.api.openapi_responses import RESOURCE_RESPONSES, UuidPath
from src.domain.user import User
from src.services.data_project_service import DataProjectService, OWNER_FLAGS

router = APIRouter(prefix="/studio", tags=["studio"], responses=RESOURCE_RESPONSES)


class CreateProjectRequest(BaseModel):
    name: str = Field(default="", max_length=300)
    investigation_id: str | None = None


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


class AttachRequest(BaseModel):
    investigation_id: str


class ShareRequest(BaseModel):
    user_id: str | None = None
    email: str | None = None
    level: str = Field(default="viewer", max_length=20)


# ── projects ────────────────────────────────────────────────────
@router.get("/projects")
@inject
async def list_projects(
    *,
    svc: FromDishka[DataProjectService],
    user: Annotated[User, Depends(get_current_user)],
    investigation_id: Annotated[str | None, Query()] = None,
) -> list[dict]:
    if investigation_id:
        projects = await svc.list_for_investigation(user.id, investigation_id)
        return [{**asdict(p), "my_access": await svc.access_flags(user.id, p)}
                for p in projects]
    projects = await svc.list_projects(user.id)  # owner-scoped
    return [{**asdict(p), "my_access": OWNER_FLAGS} for p in projects]


@router.post("/projects", status_code=201)
@inject
async def create_project(
    body: CreateProjectRequest,
    *,
    svc: FromDishka[DataProjectService],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    proj = await svc.create_project(user.id, body.name, body.investigation_id)
    return {**asdict(proj), "my_access": OWNER_FLAGS}


@router.get("/projects/{project_id}")
@inject
async def get_project(
    project_id: UuidPath,
    *,
    svc: FromDishka[DataProjectService],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    proj = await svc.get_project(user.id, project_id)
    return {**asdict(proj), "my_access": await svc.access_flags(user.id, proj)}


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


# ── investigation attach + sharing ──────────────────────────────
@router.post("/projects/{project_id}/attach", status_code=201)
@inject
async def attach_project(
    project_id: UuidPath,
    body: AttachRequest,
    *,
    svc: FromDishka[DataProjectService],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    await svc.attach(user.id, project_id, body.investigation_id)
    return {"status": "ok"}


@router.post("/projects/{project_id}/detach", status_code=201)
@inject
async def detach_project(
    project_id: UuidPath,
    *,
    svc: FromDishka[DataProjectService],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    await svc.detach(user.id, project_id)
    return {"status": "ok"}


@router.get("/projects/{project_id}/access")
@inject
async def list_project_access(
    project_id: UuidPath,
    *,
    svc: FromDishka[DataProjectService],
    user: Annotated[User, Depends(get_current_user)],
) -> list[dict]:
    return await svc.list_grants(user.id, project_id)


@router.post("/projects/{project_id}/access", status_code=201)
@inject
async def share_project(
    project_id: UuidPath,
    body: ShareRequest,
    *,
    svc: FromDishka[DataProjectService],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    await svc.share(user.id, project_id, body.user_id, target_email=body.email, level=body.level)
    return {"status": "ok"}


@router.delete("/projects/{project_id}/access/{target_uid}", status_code=204)
@inject
async def revoke_project_access(
    project_id: UuidPath,
    target_uid: UuidPath,
    *,
    svc: FromDishka[DataProjectService],
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    await svc.revoke(user.id, project_id, target_uid)


@router.get("/projects/{project_id}/effective-access")
@inject
async def project_effective_access(
    project_id: UuidPath,
    *,
    svc: FromDishka[DataProjectService],
    user: Annotated[User, Depends(get_current_user)],
) -> list[dict]:
    return await svc.effective_access(user.id, project_id)
