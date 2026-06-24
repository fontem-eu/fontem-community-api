"""Dossier endpoints — thin tree-of-articles structuring.

Handlers delegate to :class:`DossierService` (the single mutation point;
every change runs through the AuthorizationService). Dossiers are owner-gated.
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
from src.services.dossier_service import DossierService

router = APIRouter(prefix="/dossiers", tags=["dossiers"], responses=RESOURCE_RESPONSES)


class CreateDossierRequest(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    investigation_id: str | None = None


class UpdateDossierRequest(BaseModel):
    name: str = Field(min_length=1, max_length=300)


class AddArticleRequest(BaseModel):
    report_id: str
    parent_id: str | None = None


class ShareRequest(BaseModel):
    user_id: str | None = None
    email: str | None = None
    level: str = "viewer"


@router.post("", status_code=201)
@inject
async def create_dossier(
    body: CreateDossierRequest,
    *,
    svc: FromDishka[DossierService],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    d = await svc.create(user.id, body.name, body.investigation_id)
    return asdict(d)


@router.get("")
@inject
async def list_dossiers(
    *,
    svc: FromDishka[DossierService],
    user: Annotated[User, Depends(get_current_user)],
) -> list[dict]:
    return [asdict(d) for d in await svc.list_mine(user.id)]


@router.get("/{dossier_id}")
@inject
async def get_dossier(
    dossier_id: UuidPath,
    *,
    svc: FromDishka[DossierService],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    d = await svc.get(user.id, dossier_id)
    tree = await svc.tree(user.id, dossier_id)
    return {**asdict(d), "articles": tree}


@router.put("/{dossier_id}")
@inject
async def update_dossier(
    dossier_id: UuidPath,
    body: UpdateDossierRequest,
    *,
    svc: FromDishka[DossierService],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    return asdict(await svc.update(user.id, dossier_id, body.name))


@router.delete("/{dossier_id}", status_code=204)
@inject
async def delete_dossier(
    dossier_id: UuidPath,
    content: Annotated[str, Query(pattern="^(cascade|orphan)$")] = "orphan",
    *,
    svc: FromDishka[DossierService],
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    await svc.delete(user.id, dossier_id, content)


@router.post("/{dossier_id}/articles", status_code=201)
@inject
async def add_article(
    dossier_id: UuidPath,
    body: AddArticleRequest,
    *,
    svc: FromDishka[DossierService],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    await svc.add_article(user.id, dossier_id, body.report_id, body.parent_id)
    return {"status": "ok"}


@router.delete("/{dossier_id}/articles/{report_id}", status_code=204)
@inject
async def remove_article(
    dossier_id: UuidPath,
    report_id: UuidPath,
    *,
    svc: FromDishka[DossierService],
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    await svc.remove_article(user.id, dossier_id, report_id)


@router.get("/{dossier_id}/access")
@inject
async def list_dossier_access(
    dossier_id: UuidPath,
    *,
    svc: FromDishka[DossierService],
    user: Annotated[User, Depends(get_current_user)],
) -> list[dict]:
    return await svc.list_grants(user.id, dossier_id)


@router.post("/{dossier_id}/access", status_code=201)
@inject
async def share_dossier(
    dossier_id: UuidPath,
    body: ShareRequest,
    *,
    svc: FromDishka[DossierService],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    await svc.share(user.id, dossier_id, body.user_id, target_email=body.email, level=body.level)
    return {"status": "ok"}


@router.delete("/{dossier_id}/access/{target_uid}", status_code=204)
@inject
async def revoke_dossier(
    dossier_id: UuidPath,
    target_uid: UuidPath,
    *,
    svc: FromDishka[DossierService],
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    await svc.revoke(user.id, dossier_id, target_uid)


@router.get("/{dossier_id}/effective-access")
@inject
async def dossier_effective_access(
    dossier_id: UuidPath,
    *,
    svc: FromDishka[DossierService],
    user: Annotated[User, Depends(get_current_user)],
) -> list[dict]:
    return await svc.effective_access(user.id, dossier_id)
