from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Annotated, Literal

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from src.api.auth import get_current_user
from src.api.openapi_responses import RESOURCE_RESPONSES, UuidPath, UuidStr
from src.domain.user import User
from src.services.moderation_service import ModerationService

router = APIRouter(tags=["moderation"], responses=RESOURCE_RESPONSES)


class CreateFlagRequest(BaseModel):
    # Enum-shaped fields land in the OpenAPI as ``enum:`` arrays so fuzz
    # tooling stops generating arbitrary strings and flagging the
    # legitimate 400 as "API rejected schema-compliant request". The
    # service layer remains the source of truth for permission checks
    # and the canonical accept-list.
    target_type: Literal["report", "data_story", "comment", "issue"]
    target_id: UuidStr
    reason: Literal["inaccurate", "spam", "harassment", "off_topic", "other"]
    details: str = Field(default="", max_length=2000)


class ResolveFlagsRequest(BaseModel):
    target_type: Literal["report", "data_story", "comment", "issue"]
    target_id: UuidStr
    action: Literal["dismiss", "remove", "warn"]


class CreateSanctionRequest(BaseModel):
    user_id: UuidStr
    type: Literal["warning", "mute", "suspend", "ban"]
    reason: str = Field(min_length=1, max_length=500)
    expires_at: datetime | None = None


@router.post("/flags", status_code=201)
@inject
async def create_flag(
    body: CreateFlagRequest,
    *,
    svc: FromDishka[ModerationService],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    flag = await svc.flag(user.id, body.target_type, body.target_id, body.reason, body.details)
    return asdict(flag)


@router.get("/moderation/queue")
@inject
async def get_queue(
    *,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=2**31 - 1)] = 0,
    svc: FromDishka[ModerationService],
    user: Annotated[User, Depends(get_current_user)],
) -> list[dict]:
    flags = await svc.get_queue(user.id, limit, offset)
    return [asdict(f) for f in flags]


# ``flag_id`` scopes the resolve action to a flag instance (single
# clicked queue row), but the service-layer call resolves *all* flags
# matching (target_type, target_id) — that's the moderation contract.
# The path parameter still has to bind for FastAPI to route the request;
# pylint just can't see that the URL placeholder needs the name.
@router.post("/moderation/queue/{flag_id}/resolve")
@inject
async def resolve_flags(
    flag_id: UuidPath,  # pylint: disable=unused-argument
    body: ResolveFlagsRequest,
    *,
    svc: FromDishka[ModerationService],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    await svc.resolve_flags(user.id, body.target_type, body.target_id, body.action)
    return {"status": "ok"}


@router.post("/moderation/sanctions", status_code=201)
@inject
async def create_sanction(
    body: CreateSanctionRequest,
    *,
    svc: FromDishka[ModerationService],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    sanction = await svc.sanction(user.id, body.user_id, body.type, body.reason, body.expires_at)
    return asdict(sanction)


@router.get("/moderation/log")
@inject
async def get_log(
    *,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=2**31 - 1)] = 0,
    svc: FromDishka[ModerationService],
    user: Annotated[User, Depends(get_current_user)],
) -> list[dict]:
    return await svc.get_log(user.id, limit, offset)
