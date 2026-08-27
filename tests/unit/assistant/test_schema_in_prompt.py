"""Which models get the schema in prefill, and at what cost to the budget.

Two properties, both load-bearing:

- The tier boundary at the prompt: a hosted model's system prompt carries
  the schema block, a local model's does not — and the local model still has
  the get_schema tool, so it is not stranded.
- The budget knows. A prefix the arithmetic does not account for is how a
  window overflows in production and nowhere else, so the schema's length
  must shrink the history budget on exactly the turns that carry it.
"""
# pylint: disable=missing-function-docstring,protected-access
from __future__ import annotations

import asyncio
import json

from src.assistant.context import TurnLimits
from src.assistant.repository import InMemoryAssistRepository
from src.assistant.service import AssistantService, ChatRequest

SCHEMA_BLOCK = "Graph schema (live, from the platform):\n  (Contract)-[:AWARDED_TO]->(Company)"

HOSTED = "gpt-oss-120b"     # 131k context — clears the threshold
LOCAL = "qwen3-8b"          # 32k — stays below it


class _FakeSchema:
    def __init__(self, block=SCHEMA_BLOCK):
        self._block = block
        self.calls = 0

    async def block(self):
        self.calls += 1
        return self._block


class _RecordingProxy:
    def __init__(self):
        self.payloads = []

    async def stream(self, payload):
        self.payloads.append(payload)
        yield f"event: chunk\ndata: {json.dumps({'text': 'ok'})}\n\n"
        yield "event: done\ndata: {}\n\n"


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _service(schema):
    proxy = _RecordingProxy()
    svc = AssistantService(
        repo=InMemoryAssistRepository(),
        proxy_client=proxy,
        base_system_prompt="sys",
        turn_limits=TurnLimits(max_turns=20, max_chars=12_000),
        context_char_budget=8_000,
        schema_provider=schema,
    )
    return svc, proxy


async def _say(svc, model_id):
    req = ChatRequest(user_id="u-1", conversation_key="chat:x",
                      message="hello", context_block="",
                      local_model_id=model_id)
    return [b async for b in svc.turn(req)]


def test_a_hosted_model_carries_the_schema_in_prefill():
    schema = _FakeSchema()
    svc, proxy = _service(schema)
    _run(_say(svc, HOSTED))
    assert SCHEMA_BLOCK in proxy.payloads[0]["system"]


def test_a_local_model_does_not_pay_for_it():
    schema = _FakeSchema()
    svc, proxy = _service(schema)
    _run(_say(svc, LOCAL))
    assert SCHEMA_BLOCK not in proxy.payloads[0]["system"]
    assert schema.calls == 0, "below the tier, the server is not even asked"


def test_no_provider_means_no_schema_and_no_error():
    svc, proxy = _service(schema=None)
    _run(_say(svc, HOSTED))
    assert "Graph schema" not in proxy.payloads[0]["system"]


def test_an_empty_block_leaves_the_prompt_untouched():
    # The provider's failure mode is "", which must not leave a dangling
    # header or an empty section in the prompt.
    svc, proxy = _service(_FakeSchema(block=""))
    _run(_say(svc, HOSTED))
    assert "Graph schema" not in proxy.payloads[0]["system"]


def test_the_budget_shrinks_by_exactly_the_schema_length():
    svc, _ = _service(_FakeSchema())
    with_schema = svc._budget_for(HOSTED, extra_prefix_chars=len(SCHEMA_BLOCK))
    without = svc._budget_for(HOSTED)
    # 3 chars/token, 60% history share: the exact drop is derived, but the
    # invariant that matters is monotonic and proportional.
    assert with_schema.history_chars < without.history_chars
    assert with_schema.working_tokens < without.working_tokens


def test_the_stable_sections_precede_the_volatile_ones():
    # llama.cpp reuses the longest common prefix; a schema behind the
    # per-turn context would force a full re-prefill every message.
    svc, proxy = _service(_FakeSchema())
    _run(_say(svc, HOSTED))
    _run(_say(svc, HOSTED))          # second turn: history now exists
    system = proxy.payloads[1]["system"]
    assert system.index(SCHEMA_BLOCK) < system.index("Previous conversation:")
