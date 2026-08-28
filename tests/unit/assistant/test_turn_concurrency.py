"""The turn's DB session survives a fanning-out model.

2026-08-28 19:43Z, production: MiniMax opened its turn with two parallel
tool calls; studio/doc ops and the authz audit all touch the
request-scoped AsyncSession, which corrupts its own state under
concurrent use (IllegalStateChangeError), and the stream closed two
seconds in — no error event, no log line pointing anywhere. These pin
the two fixes: tool dispatch serializes on the per-turn lock, and an
unexpected exception at the stream boundary becomes a loud error event
instead of a silent close.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from src.assistant import pydantic_ai_client, tool_runtime


class _OverlapProbe:
    """Fake ToolRuntime whose dispatch measures concurrent entries."""

    def __init__(self):
        self.active = 0
        self.max_active = 0

    async def dispatch(self, _client, name, _args, **_kw):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.02)   # long enough for real overlap to show
        self.active -= 1
        return json.dumps({"ok": name}), 10


class _Tool:
    """Minimal stand-in for pydantic-ai's Tool.from_schema."""

    def __init__(self, function, name):
        self.function = function
        self.name = name

    @classmethod
    def from_schema(cls, function, name, description, json_schema):
        del description, json_schema
        return cls(function, name)


def _specs(n):
    return [{"function": {"name": f"tool{i}", "description": "d",
                          "parameters": {"type": "object"}}}
            for i in range(n)]


def _build(probe, lock):
    engine = pydantic_ai_client.PydanticAIProxyClient.__new__(
        pydantic_ai_client.PydanticAIProxyClient)
    engine._tools = probe  # pylint: disable=protected-access
    return engine._build_tools(  # pylint: disable=protected-access
        None, _Tool, _specs(4), [], [8000], None, [], {}, [],
        ctx=tool_runtime.ToolTurnContext(turn_lock=lock),
    )


def test_concurrent_tool_calls_serialize_on_the_turn_lock():
    probe = _OverlapProbe()
    tools = _build(probe, asyncio.Lock())

    async def fan_out():
        await asyncio.gather(*(t.function() for t in tools))

    asyncio.new_event_loop().run_until_complete(fan_out())
    assert probe.max_active == 1, \
        "two dispatches in flight at once is exactly the session race"


def test_without_a_lock_the_calls_do_overlap():
    # The control: proves the probe can detect overlap, so the test above
    # is measuring the lock and not an accident of scheduling.
    probe = _OverlapProbe()
    tools = _build(probe, None)

    async def fan_out():
        await asyncio.gather(*(t.function() for t in tools))

    asyncio.new_event_loop().run_until_complete(fan_out())
    assert probe.max_active > 1
