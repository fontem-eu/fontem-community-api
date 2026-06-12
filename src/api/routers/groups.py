"""Groups endpoints.

Routes delegate to :class:`GroupService`, which is the single place
group state can change. The service runs every mutation through the
:class:`AuthorizationService` so the policy table (see
``src/services/authz/policy.py``) is the source of truth for who can
do what — no per-route ACL logic, no router-level role checks.

The handlers are intentionally thin: pull the user from the JWT,
hand off to the service, translate domain exceptions into HTTP
status codes. NotFound / PermissionDenied / InvalidInput / Conflict
bubble to the app-level exception handlers and become 404 / 403 /
400 / 409 respectively.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Annotated

from dishka.integrations.fastapi import FromDishka, inject

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from src.api.auth import get_current_user
from src.api.openapi_responses import RESOURCE_RESPONSES, UuidPath
from src.domain.user import User
from src.services.group_service import GroupService

router = APIRouter(prefix="/groups", tags=["groups"], responses=RESOURCE_RESPONSES)


class CreateGroupRequest(BaseModel):
    name: str
    description: str = ""


class AddMemberRequest(BaseModel):
    user_id: str


@router.post("", status_code=201)
@inject
async def create_group(
    body: CreateGroupRequest,
    *,
    svc: FromDishka[GroupService],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    group = await svc.create(user.id, body.name, body.description)
    return asdict(group)


@router.get("/{group_id}")
@inject
async def get_group(
    group_id: UuidPath,
    *,
    svc: FromDishka[GroupService],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    group = await svc.get(user.id, group_id)
    return asdict(group)


@router.get("/{group_id}/members")
@inject
async def list_members(
    group_id: UuidPath,
    *,
    svc: FromDishka[GroupService],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """List the user-ids of group members. Owner-only — the policy
    denies this to non-owners with a 403."""
    members = await svc.list_members(user.id, group_id)
    return {"members": members}


@router.post("/{group_id}/members", status_code=201)
@inject
async def add_member(
    group_id: UuidPath,
    body: AddMemberRequest,
    *,
    svc: FromDishka[GroupService],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Add ``body.user_id`` to ``group_id``.

    Closes the IDOR documented in the 2026-06-11 security review.
    The service requires GROUPS_MANAGE_MEMBERS on the group, which
    the policy gates to the creator (or admin). Adding a
    non-existent user produces a clean 404 instead of the legacy
    500 from a Postgres FK violation.
    """
    await svc.add_member(user.id, group_id, body.user_id)
    return {"status": "ok"}


@router.delete("/{group_id}/members/{uid}", status_code=204)
@inject
async def remove_member(
    group_id: UuidPath,
    uid: UuidPath,
    *,
    svc: FromDishka[GroupService],
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    await svc.remove_member(user.id, group_id, uid)
