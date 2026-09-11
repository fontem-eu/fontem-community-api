"""A reasoning model's working-out is not its answer.

In production — the conversation "Spending with Israel", 2026-09-11 — one
assistant message was stored as 118,236 characters, beginning

    The user wants me to:
    1. Create queries and plots in Data Studio ...

and ending, with no separator at all,

    OK let me tell the user.All three cards live. Apply them in any order —

pydantic-ai hands a reasoning model's thinking over as a ThinkingPart, which
carries the SAME fields as a TextPart (`content`, `content_delta`). The
engine read those fields without asking which kind of part it had, so every
word of reasoning was streamed as answer, shown as answer, stored as the
answer, and replayed to the model as history on the next turn.
"""
# pylint: disable=missing-function-docstring,protected-access
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace as NS

from src.assistant.context import TurnLimits
from src.assistant.langgraph_client import LangGraphProxyClient
from src.assistant.pydantic_ai_client import PydanticAIProxyClient
from src.assistant.repository import InMemoryAssistRepository
from src.assistant.service import AssistantService, ChatRequest


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _events(lines):
    return [line.split("\n", 1)[0].replace("event: ", "") for line in lines]


# ── the pydantic-ai engine (what production runs) ──────────────

def _pydantic(kind, **kw):
    state = {"text_len": 0, "streaming": False, "name_cache": {}, "usage": {}}
    ev = NS(event_kind=kind, **kw)
    return PydanticAIProxyClient._translate(
        PydanticAIProxyClient.__new__(PydanticAIProxyClient), ev, state, 0.0), state


def test_a_thinking_part_goes_out_as_thinking():
    out, state = _pydantic("part_start",
                           part=NS(part_kind="thinking", content="The user wants"))
    assert _events(out) == ["thinking"]
    assert state["text_len"] == 0, "reasoning is not answer text"


def test_a_thinking_delta_goes_out_as_thinking():
    out, _ = _pydantic("part_delta",
                       delta=NS(part_delta_kind="thinking", content_delta=" me to"))
    assert _events(out) == ["thinking"]


def test_the_answer_still_goes_out_as_chunks():
    out, state = _pydantic("part_start", part=NS(part_kind="text", content="Hello"))
    assert "chunk" in _events(out)
    assert state["text_len"] == 5


# ── the langgraph engine (parity) ──────────────────────────────

def _langgraph(msg):
    state = {"streaming": False, "text_len": 0,
             "usage": {"input_tokens": 0, "output_tokens": 0}}
    client = LangGraphProxyClient.__new__(LangGraphProxyClient)
    return _events(client._on_message((msg, {}), state, 0.0)), state


def test_langgraph_routes_reasoning_content_to_thinking():
    events, state = _langgraph(NS(content="", additional_kwargs={
        "reasoning_content": "The user wants"}))
    assert events == ["thinking"] and state["text_len"] == 0


def test_a_reasoning_block_with_a_text_key_is_not_leaked_as_answer():
    # The join used to take the `text` of EVERY block.
    events, state = _langgraph(NS(content=[
        {"type": "reasoning", "text": "secret working"},
        {"type": "text", "text": "Answer"}], additional_kwargs={}))
    assert events == ["thinking", "status", "chunk"]
    assert state["text_len"] == len("Answer")


# ── what is stored ─────────────────────────────────────────────

def _sse(event, data):
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


class _Proxy:
    """A turn shaped like the production one: think, act, think, answer."""

    async def stream(self, _payload):
        yield _sse("thinking", {"text": "I should read the article first."})
        yield _sse("tool_result", {"tool": "mcp__gmr__read_document",
                                   "args": {}, "result": "{}", "call_id": "c1"})
        yield _sse("thinking", {"text": "Now I know what is there."})
        yield _sse("chunk", {"text": "All three cards are live."})
        yield "event: done\ndata: {}\n\n"


def _turn():
    repo = InMemoryAssistRepository()
    svc = AssistantService(repo=repo, proxy_client=_Proxy(),
                           base_system_prompt="sys",
                           turn_limits=TurnLimits(max_turns=20, max_chars=12_000),
                           context_char_budget=8_000)
    req = ChatRequest(user_id="u-1", conversation_key="chat:x",
                      message="hi", context_block="", local_model_id="qwen3-8b")

    async def go():
        return [line async for line in svc.turn(req)]
    _run(go())
    return repo._messages


def test_the_stored_answer_is_only_the_answer():
    # The row that is replayed as history next turn.
    answer = next(m for m in _turn() if m.role == "assistant")
    assert answer.content == "All three cards are live."
    assert "I should read" not in answer.content


def test_reasoning_is_kept_on_the_row_it_led_to():
    rows = _turn()
    tool = next(m for m in rows if m.role == "tool")
    answer = next(m for m in rows if m.role == "assistant")
    assert tool.extras["reasoning"] == "I should read the article first."
    assert answer.extras["reasoning"] == "Now I know what is there."


def test_a_row_that_followed_no_reasoning_is_unchanged():
    from src.assistant.service import _reasoning_extras  # pylint: disable=import-outside-toplevel
    assert _reasoning_extras("") is None
    assert _reasoning_extras("  \n ") is None
