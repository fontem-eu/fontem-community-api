"""AssistantService contract tests.

These verify the turn-handling orchestration without touching a real
LLM. A ``FakeProxyClient`` captures every request and yields a
scripted stream of SSE lines. We assert:

  * the service writes a user row before calling the proxy
  * the system prompt forwarded to the proxy contains the base prompt,
    the budgeted context, and the truncated history
  * the assistant row is persisted with the streamed text
  * token counts are recorded (estimate or real)
  * the caller-visible stream is the proxy's stream, unmodified
"""
# pylint: disable=missing-class-docstring,missing-function-docstring,protected-access,unused-import,too-few-public-methods
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import pytest

from src.assistant.context import TurnLimits
from src.assistant.repository import InMemoryAssistRepository
from src.assistant.service import AssistantService, ChatRequest


NOW = datetime(2026, 4, 11, 12, 0, 0, tzinfo=timezone.utc)


# ── Fake proxy client ──────────────────────────────────────────

@dataclass
class CapturedCall:
    payload: dict
    headers: dict = field(default_factory=dict)


class FakeProxyClient:
    """Captures requests and yields scripted SSE events."""

    def __init__(self, scripted_events: list[str] | None = None) -> None:
        self.calls: list[CapturedCall] = []
        self._scripted = scripted_events or [
            "event: chunk\ndata: {\"text\": \"Hello\"}\n\n",
            "event: chunk\ndata: {\"text\": \" there\"}\n\n",
            "event: done\ndata: {}\n\n",
        ]

    async def stream(self, payload: dict):
        self.calls.append(CapturedCall(payload=payload))
        for line in self._scripted:
            yield line


# ── Fixture helpers ────────────────────────────────────────────

def _make_service(proxy: FakeProxyClient | None = None) -> AssistantService:
    repo = InMemoryAssistRepository(now_provider=lambda: NOW)
    client = proxy or FakeProxyClient()
    return AssistantService(
        repo=repo,
        proxy_client=client,
        base_system_prompt="You are a research assistant.",
        turn_limits=TurnLimits(max_turns=10, max_chars=5000),
        context_char_budget=3000,
    )


# ── Tests ──────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestTurn:

    async def test_first_turn_records_user_then_assistant(self):
        proxy = FakeProxyClient()
        service = _make_service(proxy)

        chunks = []
        async for line in service.turn(
            ChatRequest(
                user_id="u1",
                conversation_key="report:abc",
                message="What is Siemens?",
                context_block="",
            )
        ):
            chunks.append(line)

        # The service relayed the proxy's events
        assert any("Hello" in c for c in chunks)

        # Both rows persisted with correct order and roles
        conv = await service._repo.find_or_create_conversation(  # pylint: disable=protected-access
            "u1", "report:abc"
        )
        messages = await service._repo.list_messages(conv.id)  # pylint: disable=protected-access
        assert [m.role for m in messages] == ["user", "assistant"]
        assert messages[0].content == "What is Siemens?"
        assert "Hello" in messages[1].content
        assert "there" in messages[1].content

    async def test_user_tokens_estimated_on_input(self):
        service = _make_service()
        async for _ in service.turn(
            ChatRequest(
                user_id="u1",
                conversation_key="k",
                message="one two three four five",
                context_block="",
            )
        ):
            pass

        conv = await service._repo.find_or_create_conversation("u1", "k")  # pylint: disable=protected-access
        msgs = await service._repo.list_messages(conv.id)  # pylint: disable=protected-access
        assert msgs[0].tokens_in is not None
        assert msgs[0].tokens_in > 0
        # Assistant row has an output estimate
        assert msgs[1].tokens_out is not None
        assert msgs[1].tokens_out > 0

    async def test_system_prompt_includes_context_block(self):
        proxy = FakeProxyClient()
        service = _make_service(proxy)

        async for _ in service.turn(
            ChatRequest(
                user_id="u1",
                conversation_key="k",
                message="q",
                context_block="Report: Siemens investigation\n\n## Section\nDetails here.",
            )
        ):
            pass

        sent = proxy.calls[0].payload
        assert "Siemens investigation" in sent["system"]
        assert "research assistant" in sent["system"]

    async def test_system_prompt_includes_history_on_second_turn(self):
        proxy = FakeProxyClient()
        service = _make_service(proxy)

        async for _ in service.turn(
            ChatRequest(user_id="u1", conversation_key="k", message="first question", context_block="")
        ):
            pass

        async for _ in service.turn(
            ChatRequest(user_id="u1", conversation_key="k", message="second question", context_block="")
        ):
            pass

        # Second turn should include the first turn in its system prompt
        second_call = proxy.calls[1]
        assert "first question" in second_call.payload["system"]
        assert "Hello" in second_call.payload["system"]  # previous assistant reply
        # And the message field is just the current question
        assert second_call.payload["message"] == "second question"

    async def test_context_budget_truncates_oversized_block(self):
        proxy = FakeProxyClient()
        service = _make_service(proxy)

        huge = "x" * 10_000
        async for _ in service.turn(
            ChatRequest(user_id="u1", conversation_key="k", message="q", context_block=huge)
        ):
            pass

        sent = proxy.calls[0].payload["system"]
        # The budget is 3000 chars in the fixture; huge content must be cut
        assert len(sent) < 10_000
        assert "truncated" in sent

    async def test_real_usage_from_sse_overrides_estimate(self):
        proxy = FakeProxyClient(scripted_events=[
            "event: chunk\ndata: {\"text\": \"Hi\"}\n\n",
            'event: usage\ndata: {"input_tokens": 100, "output_tokens": 50}\n\n',
            "event: done\ndata: {}\n\n",
        ])
        service = _make_service(proxy)

        async for _ in service.turn(
            ChatRequest(user_id="u1", conversation_key="k", message="q", context_block="")
        ):
            pass

        conv = await service._repo.find_or_create_conversation("u1", "k")  # pylint: disable=protected-access
        msgs = await service._repo.list_messages(conv.id)  # pylint: disable=protected-access
        assert msgs[0].tokens_in == 100
        assert msgs[1].tokens_out == 50

    async def test_realistic_proxy_stream_all_events(self):
        # Mirrors the actual claude-proxy.py event sequence: status at start,
        # tool_use status updates as Claude calls MCP tools, chunks with the
        # text response, a final usage event with real token counts, and a
        # done marker. All must pass through to the caller and the assistant
        # row must carry the real token counts.
        events = [
            'event: status\ndata: {"phase": "connecting", "detail": "Starting..."}\n\n',
            'event: status\ndata: {"phase": "thinking", "detail": "Processing"}\n\n',
            'event: status\ndata: {"phase": "tool_use", "tool": "mcp__gmr__search_entities", "detail": "Searching entities"}\n\n',
            'event: chunk\ndata: {"text": "Found "}\n\n',
            'event: chunk\ndata: {"text": "VINCI with 42 contracts."}\n\n',
            'event: usage\ndata: {"input_tokens": 250, "output_tokens": 18}\n\n',
            'event: done\ndata: {"done": true}\n\n',
        ]
        proxy = FakeProxyClient(scripted_events=events)
        service = _make_service(proxy)

        relayed = []
        async for line in service.turn(
            ChatRequest(user_id="u1", conversation_key="k", message="search VINCI", context_block="")
        ):
            relayed.append(line)

        # The full sequence must reach the caller (frontend needs status events for UX)
        joined = "".join(relayed)
        assert "connecting" in joined
        assert "tool_use" in joined
        assert "mcp__gmr__search_entities" in joined
        assert "Found " in joined
        assert "42 contracts" in joined
        assert "done" in joined

        # The assistant row must carry the accumulated text and the REAL token counts
        conv = await service._repo.find_or_create_conversation(  # pylint: disable=protected-access
            "u1", "k"
        )
        msgs = await service._repo.list_messages(conv.id)  # pylint: disable=protected-access
        assert len(msgs) == 2
        assert msgs[1].role == "assistant"
        assert "VINCI" in msgs[1].content
        assert msgs[1].tokens_out == 18  # real, not estimated
        assert msgs[0].tokens_in == 250   # reconciled from usage event

    async def test_proxy_error_still_records_user_row(self):
        class ErroringProxy:
            calls: list = []
            async def stream(self, payload):
                self.calls.append(payload)
                # Emit an error event before any chunks
                yield "event: error\ndata: {\"error\": \"upstream timeout\"}\n\n"

        repo = InMemoryAssistRepository(now_provider=lambda: NOW)
        service = AssistantService(
            repo=repo,
            proxy_client=ErroringProxy(),
            base_system_prompt="x",
            turn_limits=TurnLimits(),
            context_char_budget=3000,
        )

        chunks = []
        async for line in service.turn(
            ChatRequest(user_id="u1", conversation_key="k", message="q", context_block="")
        ):
            chunks.append(line)

        assert any("upstream timeout" in c for c in chunks)
        conv = await repo.find_or_create_conversation("u1", "k")
        msgs = await repo.list_messages(conv.id)
        # User row persisted; assistant row NOT persisted (no content to save)
        assert len(msgs) == 1
        assert msgs[0].role == "user"


@pytest.mark.asyncio
class TestUsageQuery:

    async def test_returns_zero_for_fresh_user(self):
        service = _make_service()
        usage = await service.usage_for_user("ghost", now=NOW)
        assert usage.tokens_1h == 0
        assert usage.tokens_24h == 0
        assert usage.tokens_7d == 0

    async def test_aggregates_after_a_turn(self):
        service = _make_service()
        async for _ in service.turn(
            ChatRequest(user_id="u1", conversation_key="k", message="hello world", context_block="")
        ):
            pass
        usage = await service.usage_for_user("u1", now=NOW)
        assert usage.tokens_1h > 0
        assert usage.tokens_24h == usage.tokens_1h
        assert usage.tokens_7d == usage.tokens_1h
