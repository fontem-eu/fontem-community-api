"""Visualization endpoints — server-side saved viz (the pocket's successor).

Thin handlers delegating to :class:`VisualizationService` (the single mutation
point; every change runs through the AuthorizationService). Viz are owner-gated;
attach/detach to an investigation needs the contributor roleability there.
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
from src.services.visualization_service import VisualizationService

router = APIRouter(prefix="/visualizations", tags=["visualizations"], responses=RESOURCE_RESPONSES)


class CreateVisualizationRequest(BaseModel):
    name: str = ""
    widget_type: str = Field(min_length=1, max_length=100)
    config: dict = Field(default_factory=dict)
    investigation_id: str | None = None


class UpdateVisualizationRequest(BaseModel):
    name: str = Field(min_length=1, max_length=300)


class AttachRequest(BaseModel):
    investigation_id: str


class ShareRequest(BaseModel):
    user_id: str | None = None
    email: str | None = None
    level: str = "viewer"


@router.post("", status_code=201)
@inject
async def create_visualization(
    body: CreateVisualizationRequest,
    *,
    svc: FromDishka[VisualizationService],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    v = await svc.create(
        user.id, body.name, body.widget_type, body.config, body.investigation_id,
    )
    return asdict(v)


@router.get("")
@inject
async def list_visualizations(
    investigation_id: Annotated[str | None, Query()] = None,
    *,
    svc: FromDishka[VisualizationService],
    user: Annotated[User, Depends(get_current_user)],
) -> list[dict]:
    items = (
        await svc.list_for_investigation(user.id, investigation_id)
        if investigation_id
        else await svc.list_mine(user.id)
    )
    return [asdict(v) for v in items]


@router.get("/{viz_id}")
@inject
async def get_visualization(
    viz_id: UuidPath,
    *,
    svc: FromDishka[VisualizationService],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    return asdict(await svc.get(user.id, viz_id))


@router.put("/{viz_id}")
@inject
async def update_visualization(
    viz_id: UuidPath,
    body: UpdateVisualizationRequest,
    *,
    svc: FromDishka[VisualizationService],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    return asdict(await svc.update(user.id, viz_id, body.name))


@router.delete("/{viz_id}", status_code=204)
@inject
async def delete_visualization(
    viz_id: UuidPath,
    *,
    svc: FromDishka[VisualizationService],
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    await svc.delete(user.id, viz_id)


@router.post("/{viz_id}/attach", status_code=201)
@inject
async def attach_visualization(
    viz_id: UuidPath,
    body: AttachRequest,
    *,
    svc: FromDishka[VisualizationService],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    await svc.attach(user.id, viz_id, body.investigation_id)
    return {"status": "ok"}


@router.post("/{viz_id}/detach", status_code=201)
@inject
async def detach_visualization(
    viz_id: UuidPath,
    *,
    svc: FromDishka[VisualizationService],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    await svc.detach(user.id, viz_id)
    return {"status": "ok"}


@router.get("/{viz_id}/access")
@inject
async def list_visualization_access(
    viz_id: UuidPath,
    *,
    svc: FromDishka[VisualizationService],
    user: Annotated[User, Depends(get_current_user)],
) -> list[dict]:
    return await svc.list_grants(user.id, viz_id)


@router.post("/{viz_id}/access", status_code=201)
@inject
async def share_visualization(
    viz_id: UuidPath,
    body: ShareRequest,
    *,
    svc: FromDishka[VisualizationService],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    await svc.share(user.id, viz_id, body.user_id, target_email=body.email, level=body.level)
    return {"status": "ok"}


@router.delete("/{viz_id}/access/{target_uid}", status_code=204)
@inject
async def revoke_visualization(
    viz_id: UuidPath,
    target_uid: UuidPath,
    *,
    svc: FromDishka[VisualizationService],
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    await svc.revoke(user.id, viz_id, target_uid)


@router.get("/{viz_id}/effective-access")
@inject
async def visualization_effective_access(
    viz_id: UuidPath,
    *,
    svc: FromDishka[VisualizationService],
    user: Annotated[User, Depends(get_current_user)],
) -> list[dict]:
    return await svc.effective_access(user.id, viz_id)
