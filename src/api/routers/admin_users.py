"""Administration: the user directory.

Read-only and admin-only. The authorisation lives in UserDirectoryService,
so a second route reaching the same data could not skip it; this module only
shapes the request and the response.
"""
from __future__ import annotations

from typing import Annotated

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, Depends, Query

from src.api.auth import get_current_user
from src.api.openapi_responses import RESOURCE_RESPONSES
from src.domain.user import User
from src.domain.user_directory import MAX_PAGE_SIZE, DirectorySort, UserDirectoryEntry
from src.services.user_directory_service import UserDirectoryService

router = APIRouter(tags=["admin"], responses=RESOURCE_RESPONSES)


def _entry_json(entry: UserDirectoryEntry) -> dict:
    """The response row, field by field.

    Written out rather than produced with asdict(): a field added to the
    directory entry should take a deliberate edit here to reach the wire,
    not arrive there by default.
    """
    return {
        "id": entry.id,
        "email": entry.email,
        "name": entry.name,
        "trust_level": entry.trust_level,
        "roles": entry.roles,
        "email_verified": entry.email_verified,
        "registered_at": entry.registered_at,
        "last_login_at": entry.last_login_at,
        "last_seen_at": entry.last_seen_at,
        "last_activity_at": entry.last_activity_at,
        "activity_count": entry.activity_count,
    }


@router.get("/admin/users")
@inject
async def list_users(
    *,
    svc: FromDishka[UserDirectoryService],
    user: Annotated[User, Depends(get_current_user)],
    sort: Annotated[DirectorySort, Query()] = "registered",
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    page = await svc.list_users(user.id, sort=sort, limit=limit, offset=offset)
    return {
        "total": page.total,
        "limit": page.limit,
        "offset": page.offset,
        "sort": page.sort,
        "users": [_entry_json(entry) for entry in page.entries],
    }
