from __future__ import annotations

from dataclasses import asdict
from typing import Annotated

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from src.api.auth import get_current_user
from src.api.openapi_responses import RESOURCE_RESPONSES, UuidPath
from src.domain.group import Group
from src.domain.user import User
from src.repositories.group_repository import GroupRepository
from src.services.exceptions import NotFound

router = APIRouter(prefix="/groups", tags=["groups"], responses=RESOURCE_RESPONSES)


class CreateGroupRequest(BaseModel):
    name: str
    description: str = ""


class AddMemberRequest(BaseModel):
    user_id: str


# ``user`` deps are auth gates: the principal isn't read directly here
# (creation/membership ops don't care *who* created/joined inside the
# handler — the service-layer ACL handles that), but the Depends still
# has to fire so the 401/403 short-circuit happens before the body runs.
# Underscore-prefix tells pylint we know it's unused.
@router.post("", status_code=201)
@inject
async def create_group(
    body: CreateGroupRequest,
    *,
    repo: FromDishka[GroupRepository],
    _user: Annotated[User, Depends(get_current_user)],
) -> dict:
    group = Group(name=body.name, description=body.description)
    group = await repo.create(group)
    return asdict(group)


@router.get("/{group_id}")
@inject
async def get_group(
    group_id: UuidPath,
    *,
    repo: FromDishka[GroupRepository],
    _user: Annotated[User, Depends(get_current_user)],
) -> dict:
    group = await repo.get_by_id(group_id)
    if group is None:
        raise NotFound(f"Group {group_id} not found")
    result = asdict(group)
    result["members"] = await repo.get_members(group_id)
    return result


@router.post("/{group_id}/members", status_code=201)
@inject
async def add_member(
    group_id: UuidPath,
    body: AddMemberRequest,
    *,
    repo: FromDishka[GroupRepository],
    _user: Annotated[User, Depends(get_current_user)],
) -> dict:
    group = await repo.get_by_id(group_id)
    if group is None:
        raise NotFound(f"Group {group_id} not found")
    await repo.add_member(group_id, body.user_id)
    return {"status": "ok"}


@router.delete("/{group_id}/members/{uid}", status_code=204)
@inject
async def remove_member(
    group_id: UuidPath,
    uid: UuidPath,
    *,
    repo: FromDishka[GroupRepository],
    _user: Annotated[User, Depends(get_current_user)],
) -> None:
    await repo.remove_member(group_id, uid)
