"""Investigations endpoints.

Thin handlers — pull the user from the JWT, delegate to
:class:`InvestigationService` (the single mutation point, which runs every
change through the AuthorizationService), and let domain exceptions become
HTTP statuses (NotFound -> 404, PermissionDenied -> 403, Conflict -> 409,
InvalidInput -> 400) via the app-level handlers.
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
from src.services.investigation_service import InvestigationService

router = APIRouter(
    prefix="/investigations", tags=["investigations"], responses=RESOURCE_RESPONSES,
)


class CreateInvestigationRequest(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    description: str = ""


class UpdateInvestigationRequest(BaseModel):
    name: str | None = Field(default=None, max_length=300)
    description: str | None = None


class AddMemberRequest(BaseModel):
    user_id: str
    can_write_stories: bool = False
    can_add_viz: bool = False
    can_administer: bool = False
    is_owner: bool = False


class UpdateMemberRequest(BaseModel):
    can_write_stories: bool = False
    can_add_viz: bool = False
    can_administer: bool = False
    is_owner: bool = False


def _with_membership(inv: dict, member) -> dict:
    return {**inv, "membership": asdict(member) if member is not None else None}


@router.post("", status_code=201)
@inject
async def create_investigation(
    body: CreateInvestigationRequest,
    *,
    svc: FromDishka[InvestigationService],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    inv = await svc.create(user.id, body.name, body.description)
    return asdict(inv)


@router.get("")
@inject
async def list_investigations(
    *,
    svc: FromDishka[InvestigationService],
    user: Annotated[User, Depends(get_current_user)],
) -> list[dict]:
    """Every investigation the caller belongs to, with their membership
    (capability flags) so the UI can show their role."""
    out: list[dict] = []
    for inv in await svc.list_mine(user.id):
        member = await svc.my_membership(user.id, inv.id)  # type: ignore[arg-type]
        out.append(_with_membership(asdict(inv), member))
    return out


@router.get("/{investigation_id}")
@inject
async def get_investigation(
    investigation_id: UuidPath,
    *,
    svc: FromDishka[InvestigationService],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    inv = await svc.get(user.id, investigation_id)
    member = await svc.my_membership(user.id, investigation_id)
    return _with_membership(asdict(inv), member)


@router.put("/{investigation_id}")
@inject
async def update_investigation(
    investigation_id: UuidPath,
    body: UpdateInvestigationRequest,
    *,
    svc: FromDishka[InvestigationService],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    inv = await svc.update_meta(user.id, investigation_id, body.name, body.description)
    return asdict(inv)


@router.delete("/{investigation_id}", status_code=204)
@inject
async def delete_investigation(
    investigation_id: UuidPath,
    content: Annotated[str, Query(pattern="^(cascade|orphan)$")] = "orphan",
    *,
    svc: FromDishka[InvestigationService],
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    await svc.delete(user.id, investigation_id, content)


@router.get("/{investigation_id}/members")
@inject
async def list_members(
    investigation_id: UuidPath,
    *,
    svc: FromDishka[InvestigationService],
    user: Annotated[User, Depends(get_current_user)],
) -> list[dict]:
    members = await svc.list_members(user.id, investigation_id)
    return [asdict(m) for m in members]


@router.post("/{investigation_id}/members", status_code=201)
@inject
async def add_member(
    investigation_id: UuidPath,
    body: AddMemberRequest,
    *,
    svc: FromDishka[InvestigationService],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    await svc.set_member(
        user.id, investigation_id, body.user_id,
        can_write_stories=body.can_write_stories, can_add_viz=body.can_add_viz,
        can_administer=body.can_administer, is_owner=body.is_owner,
    )
    return {"status": "ok"}


@router.put("/{investigation_id}/members/{uid}")
@inject
async def update_member(
    investigation_id: UuidPath,
    uid: UuidPath,
    body: UpdateMemberRequest,
    *,
    svc: FromDishka[InvestigationService],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    await svc.set_member(
        user.id, investigation_id, uid,
        can_write_stories=body.can_write_stories, can_add_viz=body.can_add_viz,
        can_administer=body.can_administer, is_owner=body.is_owner,
    )
    return {"status": "ok"}


@router.delete("/{investigation_id}/members/{uid}", status_code=204)
@inject
async def remove_member(
    investigation_id: UuidPath,
    uid: UuidPath,
    *,
    svc: FromDishka[InvestigationService],
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    await svc.remove_member(user.id, investigation_id, uid)
