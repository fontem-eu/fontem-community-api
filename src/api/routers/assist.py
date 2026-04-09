"""LLM assist endpoints for AI-powered report writing."""
from __future__ import annotations

import json
import os
from collections.abc import AsyncGenerator

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select, and_

from src.api.auth import get_current_user
from src.api.dependencies import get_db_session
from src.domain.user import User
from src.infra.postgres.models import ConversationModel
from src.services.llm_service import LLMService, TOOLS, CLAUDE_PROXY_URL, SYSTEM_PROMPT

router = APIRouter(prefix="/assist", tags=["assist"])

_llm = LLMService()


class ChatRequest(BaseModel):
    message: str
    history: list[dict] = []
    report_context: str | None = None


class ChatResponse(BaseModel):
    content: str
    tool_calls_made: int = 0
    suggestions: list[dict] = []


@router.post("/chat", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    user: User = Depends(get_current_user),
) -> ChatResponse:
    """Send a message to the AI assistant (blocking)."""
    result = await _llm.chat(
        user_message=body.message,
        history=body.history,
        report_context=body.report_context,
    )
    return ChatResponse(
        content=result["content"],
        tool_calls_made=result.get("tool_calls_made", 0),
        suggestions=result.get("suggestions", []),
    )


@router.post("/chat/stream")
async def chat_stream(
    body: ChatRequest,
    user: User = Depends(get_current_user),
) -> StreamingResponse:
    """Stream AI assistant response via SSE."""
    system = SYSTEM_PROMPT
    if body.report_context:
        system += f"\n\nCurrent report context:\n{body.report_context}"

    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
                async with client.stream(
                    "POST",
                    f"{CLAUDE_PROXY_URL}/chat/stream",
                    json={"message": body.message, "system": system},
                ) as resp:
                    async for line in resp.aiter_lines():
                        if line.startswith("event:") or line.startswith("data:"):
                            yield line + "\n"
                        elif line == "":
                            yield "\n"
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            yield f"event: error\ndata: {json.dumps({'error': str(exc)[:200]})}\n\n"
        yield f"event: done\ndata: {json.dumps({'done': True})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/tools")
async def list_tools(
    user: User = Depends(get_current_user),
) -> list[dict]:
    """List available tools the assistant can use."""
    return [{"name": t["name"], "description": t["description"]} for t in TOOLS]


# ── Conversation persistence ─────────────────────────────────────


class ConversationResponse(BaseModel):
    id: str
    report_id: str
    messages: list[dict]
    updated_at: str


class SaveConversationRequest(BaseModel):
    messages: list[dict]


@router.get("/conversations/{report_id}", response_model=ConversationResponse | None)
async def get_conversation(
    report_id: str,
    user: User = Depends(get_current_user),
    session=Depends(get_db_session),
):
    """Get the conversation for a user+report pair."""
    result = await session.execute(
        select(ConversationModel).where(
            and_(
                ConversationModel.user_id == user.id,
                ConversationModel.report_id == report_id,
            )
        )
    )
    row = result.scalar_one_or_none()
    if not row:
        return None
    return ConversationResponse(
        id=row.id,
        report_id=row.report_id,
        messages=row.messages,
        updated_at=row.updated_at.isoformat(),
    )


@router.put("/conversations/{report_id}", response_model=ConversationResponse)
async def save_conversation(
    report_id: str,
    body: SaveConversationRequest,
    user: User = Depends(get_current_user),
    session=Depends(get_db_session),
):
    """Save/update the conversation for a user+report pair."""
    result = await session.execute(
        select(ConversationModel).where(
            and_(
                ConversationModel.user_id == user.id,
                ConversationModel.report_id == report_id,
            )
        )
    )
    row = result.scalar_one_or_none()
    if row:
        row.messages = body.messages
        from datetime import datetime, timezone
        row.updated_at = datetime.now(timezone.utc)
    else:
        row = ConversationModel(
            user_id=user.id,
            report_id=report_id,
            messages=body.messages,
        )
        session.add(row)
    await session.flush()
    return ConversationResponse(
        id=row.id,
        report_id=row.report_id,
        messages=row.messages,
        updated_at=row.updated_at.isoformat(),
    )
