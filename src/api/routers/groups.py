from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from src.api.auth import get_current_user
from src.api.dependencies import get_group_repo
from src.domain.group import Group
from src.domain.user import User
from src.repositories.group_repository import GroupRepository
from src.services.exceptions import NotFound

router = APIRouter(prefix="/groups", tags=["groups"])


class CreateGroupRequest(BaseModel):
    name: str
    description: str = ""


class AddMemberRequest(BaseModel):
    user_id: str


@router.post("", status_code=201)
async def create_group(
    body: CreateGroupRequest,
    user: User = Depends(get_current_user),
    repo: GroupRepository = Depends(get_group_repo),
) -> dict:
    group = Group(name=body.name, description=body.description)
    group = await repo.create(group)
    return asdict(group)


@router.get("/{group_id}")
async def get_group(
    group_id: str,
    user: User = Depends(get_current_user),
    repo: GroupRepository = Depends(get_group_repo),
) -> dict:
    group = await repo.get_by_id(group_id)
    if group is None:
        raise NotFound(f"Group {group_id} not found")
    result = asdict(group)
    result["members"] = await repo.get_members(group_id)
    return result


@router.post("/{group_id}/members", status_code=201)
async def add_member(
    group_id: str,
    body: AddMemberRequest,
    user: User = Depends(get_current_user),
    repo: GroupRepository = Depends(get_group_repo),
) -> dict:
    group = await repo.get_by_id(group_id)
    if group is None:
        raise NotFound(f"Group {group_id} not found")
    await repo.add_member(group_id, body.user_id)
    return {"status": "ok"}


@router.delete("/{group_id}/members/{uid}", status_code=204)
async def remove_member(
    group_id: str,
    uid: str,
    user: User = Depends(get_current_user),
    repo: GroupRepository = Depends(get_group_repo),
) -> None:
    await repo.remove_member(group_id, uid)
