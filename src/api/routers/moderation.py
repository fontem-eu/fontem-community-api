from __future__ import annotations

from dataclasses import asdict
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from src.api.auth import get_current_user
from src.api.dependencies import get_moderation_service
from src.domain.user import User
from src.services.moderation_service import ModerationService

router = APIRouter(tags=["moderation"])


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
async def create_flag(
    body: CreateFlagRequest,
    user: User = Depends(get_current_user),
    svc: ModerationService = Depends(get_moderation_service),
) -> dict:
    flag = await svc.flag(user.id, body.target_type, body.target_id, body.reason, body.details)
    return asdict(flag)


@router.get("/moderation/queue")
async def get_queue(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    svc: ModerationService = Depends(get_moderation_service),
) -> list[dict]:
    flags = await svc.get_queue(user.id, limit, offset)
    return [asdict(f) for f in flags]


@router.post("/moderation/queue/{flag_id}/resolve")
async def resolve_flags(
    flag_id: str,
    body: ResolveFlagsRequest,
    user: User = Depends(get_current_user),
    svc: ModerationService = Depends(get_moderation_service),
) -> dict:
    await svc.resolve_flags(user.id, body.target_type, body.target_id, body.action)
    return {"status": "ok"}


@router.post("/moderation/sanctions", status_code=201)
async def create_sanction(
    body: CreateSanctionRequest,
    user: User = Depends(get_current_user),
    svc: ModerationService = Depends(get_moderation_service),
) -> dict:
    sanction = await svc.sanction(user.id, body.user_id, body.type, body.reason, body.expires_at)
    return asdict(sanction)


@router.get("/moderation/log")
async def get_log(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    svc: ModerationService = Depends(get_moderation_service),
) -> list[dict]:
    return await svc.get_log(user.id, limit, offset)
