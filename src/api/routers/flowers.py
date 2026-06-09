"""Flowers (Medium-style clap) endpoints.

Two routes, both keyed by the report id:

  GET  /data-stories/{report_id}/flowers  → {total, mine}
  POST /data-stories/{report_id}/flowers  → {total, mine}  (mine += 1)

The router is mounted twice in ``app.py`` — at /data-stories (the
canonical prefix) and at /reports (the deprecated alias kept during
the rename window) — so existing API clients keep working without a
breaking change. The flowers schema mirrors the tag-feature
conventions (UuidPath on the path param, RESOURCE_RESPONSES on the
router, FromDishka for service injection, Annotated for FastAPI's
auth dependencies). NotFound and InvalidInput are translated by
app-level handlers, so route handlers stay free of try/except noise.
"""
from __future__ import annotations

from typing import Annotated

from dishka.integrations.fastapi import FromDishka, inject

from fastapi import APIRouter, Depends

from src.api.auth import get_current_user, get_optional_user
from src.api.openapi_responses import RESOURCE_RESPONSES, UuidPath
from src.domain.user import User
from src.services.flower_service import FlowerService, MAX_FLOWERS_PER_USER


router = APIRouter(tags=["flowers"], responses=RESOURCE_RESPONSES)


@router.get(
    "/{report_id}/flowers",
    summary="Get current flower count for a story (and per-user count if signed-in)",
    # GET is intentionally open: the total is public information for
    # any public-visibility story. mine is per-user and only meaningful
    # when authenticated; tell OpenAPI consumers the GET has no
    # security requirement so schemathesis stops flagging unauth 200s.
    openapi_extra={"security": []},
)
@inject
async def get_flowers(
    report_id: UuidPath,
    *,
    svc: FromDishka[FlowerService],
    user: Annotated[User | None, Depends(get_optional_user)],
) -> dict:
    # NotFound bubbles up to the app-level handler → 404.
    state = await svc.get_state(
        user.id if user is not None else None,
        report_id,
    )
    return {
        "total": state["total"],
        "mine": state["mine"],
        "max_per_user": MAX_FLOWERS_PER_USER,
    }


@router.post(
    "/{report_id}/flowers",
    status_code=200,
    summary="Give one flower to a story (cap: 50 per user)",
)
@inject
async def give_flower(
    report_id: UuidPath,
    *,
    svc: FromDishka[FlowerService],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    # NotFound → 404 / InvalidInput (cap hit) → 400 via app-level handlers.
    state = await svc.give(user.id, report_id)
    return {
        "total": state["total"],
        "mine": state["mine"],
        "max_per_user": MAX_FLOWERS_PER_USER,
    }
