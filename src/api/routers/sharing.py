from __future__ import annotations

from dataclasses import asdict
from typing import Annotated

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from src.api.auth import get_current_user
from src.api.openapi_responses import RESOURCE_RESPONSES, UuidPath
from src.domain.user import User
from src.repositories.permission_repository import PermissionRepository
from src.repositories.report_repository import ReportRepository
from src.services.authz import Action, AuthorizationService, ResourceRef
from src.services.exceptions import NotFound

# Mounted by app.py at /data-stories (canonical) and /reports (legacy
# alias). The {report_id} path parameter name is internal — the URL
# path users see is /data-stories/<uuid>/access.
router = APIRouter(prefix="/{report_id}/access", tags=["sharing"], responses=RESOURCE_RESPONSES)


class SetAccessRequest(BaseModel):
    user_id: str | None = None
    group_id: str | None = None
    level: str  # owner, editor, commenter, viewer


async def _require_share(
    user_id: str,
    report_id: str,
    reports: ReportRepository,
    authz: AuthorizationService,
) -> None:
    """Single seam for the three share endpoints: load the report,
    surface 404 if it's gone, and route through the authz service so
    every grant-mutation lands in the audit log."""
    report = await reports.get_by_id(report_id)
    if report is None:
        raise NotFound(f"Report {report_id} not found")
    principal = await authz.principal(user_id)
    await authz.require(
        principal, Action.STORIES_SHARE, ResourceRef.for_story(report),
    )


@router.get("")
@inject
# pylint: disable-next=too-many-arguments,too-many-positional-arguments
async def list_access(
    report_id: UuidPath,
    *,
    reports_repo: FromDishka[ReportRepository],
    authz: FromDishka[AuthorizationService],
    perms_repo: FromDishka[PermissionRepository],
    user: Annotated[User, Depends(get_current_user)],
) -> list[dict]:
    await _require_share(user.id, report_id, reports_repo, authz)
    grants = await perms_repo.list_collaborators(report_id)
    return [asdict(g) for g in grants]


@router.post("", status_code=201)
@inject
# pylint: disable-next=too-many-arguments,too-many-positional-arguments
async def set_access(
    report_id: UuidPath,
    body: SetAccessRequest,
    *,
    reports_repo: FromDishka[ReportRepository],
    authz: FromDishka[AuthorizationService],
    perms_repo: FromDishka[PermissionRepository],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    await _require_share(user.id, report_id, reports_repo, authz)
    if body.user_id:
        await perms_repo.set_user_access(report_id, body.user_id, body.level)
    elif body.group_id:
        await perms_repo.set_group_access(report_id, body.group_id, body.level)
    return {"status": "ok"}


@router.delete("/{access_id}", status_code=204)
@inject
# pylint: disable-next=too-many-arguments,too-many-positional-arguments
async def remove_access(
    report_id: UuidPath,
    access_id: UuidPath,
    *,
    reports_repo: FromDishka[ReportRepository],
    authz: FromDishka[AuthorizationService],
    perms_repo: FromDishka[PermissionRepository],
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    await _require_share(user.id, report_id, reports_repo, authz)
    # Find the grant and remove it
    grants = await perms_repo.list_collaborators(report_id)
    for g in grants:
        if g.id == access_id:
            if g.user_id:
                await perms_repo.remove_user_access(report_id, g.user_id)
            elif g.group_id:
                await perms_repo.remove_group_access(report_id, g.group_id)
            break
