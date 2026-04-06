"""LLM assist endpoints for AI-powered report writing."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from src.api.auth import get_current_user
from src.domain.user import User
from src.services.llm_service import LLMService, TOOLS

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
    """Send a message to the AI assistant."""
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


@router.get("/tools")
async def list_tools(
    user: User = Depends(get_current_user),
) -> list[dict]:
    """List available tools the assistant can use."""
    return [{"name": t["name"], "description": t["description"]} for t in TOOLS]
