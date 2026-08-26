"""Summarising is conditional, and the condition is overflow.

The point of deriving the window from the model is that a large context stops
paying for machinery it does not need. A 1M-token model that still summarised
every turn would have the cost without the reason.
"""
# pylint: disable=missing-function-docstring,protected-access
from __future__ import annotations

import asyncio
import json

from src.assistant.context import TurnLimits
from src.assistant.repository import InMemoryAssistRepository
from src.assistant.service import AssistantService, ChatRequest


def _sse(event, data):
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


class _RecordingProxy:
    """Answers every stream, and records the payloads it was given."""

    def __init__(self, reply="ok"):
        self.payloads: list[dict] = []
        self._reply = reply

    async def stream(self, payload):
        self.payloads.append(payload)
        yield _sse("chunk", {"text": self._reply})
        yield _sse("done", {})


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _service(proxy, repo, max_chars, tiny_context=True):
    """A service whose history budget is `max_chars`.

    The budget is derived from the model, so setting `turn_limits` alone does
    not decide it — that is the whole point of phase 4, and a test that set
    only the limit would silently exercise the 8B's 46,554-character window
    instead. An impossibly large fixed prefix drives `derive` into its floor
    branch, where the floor IS `turn_limits.max_chars`.
    """
    return AssistantService(
        repo=repo,
        proxy_client=proxy,
        base_system_prompt="sys",
        turn_limits=TurnLimits(max_turns=20, max_chars=max_chars),
        context_char_budget=8_000,
        fixed_prefix_chars=10_000_000 if tiny_context else 7_176,
    )


async def _say(svc, message="hello"):
    req = ChatRequest(
        user_id="u-1", conversation_key="chat:x", message=message, context_block="",
    )
    return [b async for b in svc.turn(req)]


def _summary_calls(proxy):
    from src.assistant import summariser
    return [p for p in proxy.payloads if p.get("system") == summariser.SYSTEM_PROMPT]


def test_a_conversation_that_fits_never_summarises():
    repo = InMemoryAssistRepository()
    proxy = _RecordingProxy()
    svc = _service(proxy, repo, max_chars=100_000, tiny_context=False)

    for _ in range(6):
        _run(_say(svc, "a short message"))

    assert _summary_calls(proxy) == [], "nothing overflowed, so nothing should be summarised"


def test_overflow_produces_a_summary_and_stores_it():
    repo = InMemoryAssistRepository()
    proxy = _RecordingProxy(reply="y" * 400)
    svc = _service(proxy, repo, max_chars=500)

    for _ in range(6):
        _run(_say(svc, "x" * 400))

    assert _summary_calls(proxy), "the window overflowed; a summary was due"
    conv = list(repo._conversations.values())[0]
    assert conv.summary, "the summary should be persisted, not recomputed each turn"
    assert conv.summary_through, "and it should record how far it reaches"


def test_the_summariser_is_not_offered_tools():
    repo = InMemoryAssistRepository()
    proxy = _RecordingProxy(reply="y" * 400)
    svc = _service(proxy, repo, max_chars=500)

    for _ in range(6):
        _run(_say(svc, "x" * 400))

    for call in _summary_calls(proxy):
        assert call.get("has_editor") is False
        assert "studio_ops" not in call


def test_a_failing_summariser_does_not_take_down_the_turn():
    class _Exploding(_RecordingProxy):
        async def stream(self, payload):
            self.payloads.append(payload)
            from src.assistant import summariser
            if payload.get("system") == summariser.SYSTEM_PROMPT:
                raise RuntimeError("summariser is down")
            yield _sse("chunk", {"text": "y" * 400})
            yield _sse("done", {})

    repo = InMemoryAssistRepository()
    proxy = _Exploding()
    svc = _service(proxy, repo, max_chars=500)

    for _ in range(6):
        out = _run(_say(svc, "x" * 400))
        assert out, "the user's turn must still be answered"

    conv = list(repo._conversations.values())[0]
    assert conv.summary == "", "a failed summary is absent, not corrupt"


def test_a_stored_summary_reaches_the_model():
    repo = InMemoryAssistRepository()
    proxy = _RecordingProxy(reply="y" * 400)
    svc = _service(proxy, repo, max_chars=500)

    for _ in range(6):
        _run(_say(svc, "x" * 400))

    from src.assistant import summariser
    turn_calls = [p for p in proxy.payloads if p.get("system") != summariser.SYSTEM_PROMPT]
    assert any(summariser.SUMMARY_PREFIX in p["system"] for p in turn_calls), \
        "the summary is useless if it never reaches the prompt"
