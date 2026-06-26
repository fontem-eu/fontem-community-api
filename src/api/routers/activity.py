from __future__ import annotations

from typing import Annotated

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, Depends, Query

from src.api.auth import get_current_user
from src.api.openapi_responses import RESOURCE_RESPONSES
from src.domain.user import User
from src.services.activity_service import ActivityService

router = APIRouter(prefix="/activity", tags=["activity"], responses=RESOURCE_RESPONSES)


@router.get("")
@inject
async def list_activity(
    *,
    svc: FromDishka[ActivityService],
    user: Annotated[User, Depends(get_current_user)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=2**31 - 1)] = 0,
) -> list[dict]:
    """The signed-in user's own create/update/delete activity, newest first."""
    return await svc.list_for_actor(user.id, limit, offset)
