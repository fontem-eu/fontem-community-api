"""HTTP routes exposed by the assistant module.

Mounted at ``/assist`` by the app. The router is deliberately thin:
it validates, dispatches to the service, and streams the result back.
No business logic lives here.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from src.api.auth import get_current_user
from src.api.openapi_responses import AUTH_RESPONSES
from src.domain.user import User
from src.assistant.service import AssistantService, ChatRequest


router = APIRouter(prefix="/assist", tags=["assist"], responses=AUTH_RESPONSES)


# ── Request / response models ─────────────────────────────────


class AssistChatBody(BaseModel):
    """Request body for POST /assist/chat/stream.

    ``conversation_key`` is an opaque caller-chosen identifier. For the
    report editor it's ``report:<uuid>``. The assistant doesn't look
    inside it.

    ``context_block`` is a pre-rendered string the caller wants to put
    in the assistant's field of view for this turn. The assistant will
    truncate it to fit its own budget before sending to the LLM.
    """

    message: str = Field(..., min_length=1)
    conversation_key: str = Field(..., min_length=1)
    context_block: str = ""


class UsageResponse(BaseModel):
    """Rolling-window token totals for the current user."""
    tokens_1h: int
    tokens_24h: int
    tokens_7d: int


class DailyUsagePoint(BaseModel):
    """A single day's token totals in a usage-history response."""
    date: str
    tokens_in: int
    tokens_out: int


class UsageHistoryResponse(BaseModel):
    """Per-day token consumption over the requested window."""
    days: int
    points: list[DailyUsagePoint]


class HistoryMessage(BaseModel):
    """A single persisted assistant/user message in a conversation history view."""
    role: str
    content: str
    created_at: str
    tokens_in: int | None = None
    tokens_out: int | None = None


# ── Endpoints ──────────────────────────────────────────────────


# Return annotation is omitted deliberately: with `from __future__ import
# annotations`, FastAPI treats the annotation as a string ForwardRef and
# hands it to Pydantic while building the OpenAPI schema, which then
# raises `PydanticUserError` (StreamingResponse is not a Pydantic type)
# and crashes /openapi.json with a 500. `response_class=` tells FastAPI
# this is a streaming endpoint without involving schema generation.
@router.post("/chat/stream", response_class=StreamingResponse)
@inject
async def chat_stream(
    body: AssistChatBody,
    *,
    service: FromDishka[AssistantService],
    user: User = Depends(get_current_user),
):
    """Stream an assistant reply via SSE."""
    req = ChatRequest(
        user_id=user.id,
        conversation_key=body.conversation_key,
        message=body.message,
        context_block=body.context_block,
    )

    async def generator() -> AsyncGenerator[str, None]:
        async for line in service.turn(req):
            yield line
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/usage", response_model=UsageResponse)
@inject
async def usage(
    *,
    service: FromDishka[AssistantService],
    user: User = Depends(get_current_user),
) -> UsageResponse:
    """Return the current user's token consumption over rolling windows."""
    snapshot = await service.usage_for_user(user.id)
    return UsageResponse(
        tokens_1h=snapshot.tokens_1h,
        tokens_24h=snapshot.tokens_24h,
        tokens_7d=snapshot.tokens_7d,
    )


@router.get("/usage-history", response_model=UsageHistoryResponse)
@inject
async def usage_history(
    days: int = Query(30, ge=1, le=365),
    *,
    service: FromDishka[AssistantService],
    user: User = Depends(get_current_user),
) -> UsageHistoryResponse:
    """Return per-day token totals for the current user over the last N days."""
    rows = await service.usage_history_for_user(user.id, days=days)
    return UsageHistoryResponse(
        days=days,
        points=[
            DailyUsagePoint(
                date=r.day.isoformat(),
                tokens_in=r.tokens_in,
                tokens_out=r.tokens_out,
            )
            for r in rows
        ],
    )


@router.delete("/conversations")
@inject
async def delete_all_conversations(
    *,
    user: User = Depends(get_current_user),
    service: FromDishka[AssistantService],
) -> dict:
    """Delete all conversation history for the current user."""
    count = await service.delete_user_conversations(user.id)
    return {"deleted": count}


@router.get("/conversations/{conversation_key:path}")
@inject
async def get_conversation(
    conversation_key: str,
    *,
    service: FromDishka[AssistantService],
    user: User = Depends(get_current_user),
) -> dict:
    """Return the full stored history for a conversation key, scoped to the user."""
    conv = await service._repo.find_or_create_conversation(  # pylint: disable=protected-access
        user.id, conversation_key
    )
    messages = await service._repo.list_messages(conv.id)  # pylint: disable=protected-access
    return {
        "conversation_key": conversation_key,
        "messages": [
            {
                "role": m.role,
                "content": m.content,
                "created_at": m.created_at.isoformat(),
                "tokens_in": m.tokens_in,
                "tokens_out": m.tokens_out,
            }
            for m in messages
        ],
    }
