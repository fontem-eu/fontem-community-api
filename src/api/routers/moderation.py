from __future__ import annotations

from dishka.integrations.fastapi import FromDishka, inject

from dataclasses import asdict
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from src.api.auth import get_current_user
from src.api.openapi_responses import AUTH_RESPONSES
from src.domain.user import User
from src.services.moderation_service import ModerationService

router = APIRouter(tags=["moderation"], responses=AUTH_RESPONSES)


class CreateFlagRequest(BaseModel):
    target_type: str  # report, comment, issue
    target_id: str
    reason: str  # inaccurate, spam, harassment, off_topic, other
    details: str = ""


class ResolveFlagsRequest(BaseModel):
    target_type: str
    target_id: str
    action: str  # dismiss, remove, warn


class CreateSanctionRequest(BaseModel):
    user_id: str
    type: str  # warning, mute, suspend, ban
    reason: str
    expires_at: datetime | None = None


@router.post("/flags", status_code=201)
@inject
async def create_flag(
    body: CreateFlagRequest,
    *,
    svc: FromDishka[ModerationService],
    user: User = Depends(get_current_user),
) -> dict:
    flag = await svc.flag(user.id, body.target_type, body.target_id, body.reason, body.details)
    return asdict(flag)


@router.get("/moderation/queue")
@inject
async def get_queue(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    *,
    svc: FromDishka[ModerationService],
    user: User = Depends(get_current_user),
) -> list[dict]:
    flags = await svc.get_queue(user.id, limit, offset)
    return [asdict(f) for f in flags]


@router.post("/moderation/queue/{flag_id}/resolve")
@inject
async def resolve_flags(
    flag_id: str,
    body: ResolveFlagsRequest,
    *,
    svc: FromDishka[ModerationService],
    user: User = Depends(get_current_user),
) -> dict:
    await svc.resolve_flags(user.id, body.target_type, body.target_id, body.action)
    return {"status": "ok"}


@router.post("/moderation/sanctions", status_code=201)
@inject
async def create_sanction(
    body: CreateSanctionRequest,
    *,
    svc: FromDishka[ModerationService],
    user: User = Depends(get_current_user),
) -> dict:
    sanction = await svc.sanction(user.id, body.user_id, body.type, body.reason, body.expires_at)
    return asdict(sanction)


@router.get("/moderation/log")
@inject
async def get_log(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    *,
    svc: FromDishka[ModerationService],
    user: User = Depends(get_current_user),
) -> list[dict]:
    return await svc.get_log(user.id, limit, offset)
