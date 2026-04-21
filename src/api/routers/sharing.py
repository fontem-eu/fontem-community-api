from __future__ import annotations

from dishka.integrations.fastapi import FromDishka, inject

from dataclasses import asdict

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from src.api.auth import get_current_user
from src.api.openapi_responses import AUTH_RESPONSES
from src.domain.user import User
from src.repositories.permission_repository import PermissionRepository
from src.services.permission_service import PermissionService

router = APIRouter(prefix="/reports/{report_id}/access", tags=["sharing"], responses=AUTH_RESPONSES)


class SetAccessRequest(BaseModel):
    user_id: str | None = None
    group_id: str | None = None
    level: str  # owner, editor, commenter, viewer


@router.get("")
@inject
async def list_access(
    report_id: str,
    *,
    perms_svc: FromDishka[PermissionService],
    perms_repo: FromDishka[PermissionRepository],
    user: User = Depends(get_current_user),
) -> list[dict]:
    await perms_svc.require(user.id, report_id, "owner")
    grants = await perms_repo.list_collaborators(report_id)
    return [asdict(g) for g in grants]


@router.post("", status_code=201)
@inject
async def set_access(
    report_id: str,
    body: SetAccessRequest,
    *,
    perms_svc: FromDishka[PermissionService],
    perms_repo: FromDishka[PermissionRepository],
    user: User = Depends(get_current_user),
) -> dict:
    await perms_svc.require(user.id, report_id, "owner")
    if body.user_id:
        await perms_repo.set_user_access(report_id, body.user_id, body.level)
    elif body.group_id:
        await perms_repo.set_group_access(report_id, body.group_id, body.level)
    return {"status": "ok"}


@router.delete("/{access_id}", status_code=204)
@inject
async def remove_access(
    report_id: str,
    access_id: str,
    *,
    perms_svc: FromDishka[PermissionService],
    perms_repo: FromDishka[PermissionRepository],
    user: User = Depends(get_current_user),
) -> None:
    await perms_svc.require(user.id, report_id, "owner")
    # Find the grant and remove it
    grants = await perms_repo.list_collaborators(report_id)
    for g in grants:
        if g.id == access_id:
            if g.user_id:
                await perms_repo.remove_user_access(report_id, g.user_id)
            elif g.group_id:
                await perms_repo.remove_group_access(report_id, g.group_id)
            break
