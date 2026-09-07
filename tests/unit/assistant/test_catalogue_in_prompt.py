"""The "What Dargle holds" block must actually reach the model.

This is a regression test in the strict sense: the block was injected, then
`2b37dcc refactor: decommission the hand-written executor` removed the two
lines that called it and nothing failed. Everything else survived -- the
CronJob that writes the prefill, the ConfigMap, the volume mount, the cache,
the formatter, and the base prompt's claim that

    A "What Dargle holds" block below lists its data ... that list, not this
    paragraph, is the authority on scope.

So for months the model was pointed at a block it was never given, and said
so out loud in an eval run: "There's no holdings block shown in the prompt".

The tests below assert the wiring end to end -- provider to payload -- rather
than the formatter in isolation, because the formatter was never what broke.
"""
# pylint: disable=missing-function-docstring,protected-access
from __future__ import annotations

import asyncio
import json

from src.assistant.context import TurnLimits
from src.assistant.repository import InMemoryAssistRepository
from src.assistant.service import AssistantService, ChatRequest

HOLDINGS = "## What Dargle holds\n\n- TED procurement notices, 2014-2026"

HOSTED = "gpt-oss-120b"     # 131k context — clears the schema threshold
LOCAL = "qwen3-8b"          # 32k — stays below it


class _FakeCatalogue:
    def __init__(self, block=HOLDINGS):
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


def _service(catalogue):
    proxy = _RecordingProxy()
    svc = AssistantService(
        repo=InMemoryAssistRepository(),
        proxy_client=proxy,
        base_system_prompt="sys",
        turn_limits=TurnLimits(max_turns=20, max_chars=12_000),
        context_char_budget=8_000,
        catalogue_provider=catalogue,
    )
    return svc, proxy


async def _say(svc, model_id, user_id="u-1"):
    req = ChatRequest(user_id=user_id, conversation_key="chat:x",
                      message="what data do you have?", context_block="",
                      local_model_id=model_id)
    return [b async for b in svc.turn(req)]


def test_the_holdings_reach_the_model():
    svc, proxy = _service(_FakeCatalogue())
    _run(_say(svc, HOSTED))
    assert HOLDINGS in proxy.payloads[0]["system"]


def test_a_small_model_gets_it_too():
    # Deliberately NOT tiered like the schema. A 32k model has the least
    # room to discover the holdings by probing, so it is the one that most
    # needs to be told -- and ~1.3k tokens is affordable even there.
    catalogue = _FakeCatalogue()
    svc, proxy = _service(catalogue)
    _run(_say(svc, LOCAL))
    assert HOLDINGS in proxy.payloads[0]["system"]
    assert catalogue.calls == 1


def test_a_signed_out_visitor_gets_it():
    # "What data do you have" is the likeliest anonymous question, and the
    # wayfinder carries the same base prompt promising this block.
    svc, proxy = _service(_FakeCatalogue())
    _run(_say(svc, LOCAL, user_id=None))
    assert HOLDINGS in proxy.payloads[0]["system"]


def test_no_provider_means_no_block_and_no_error():
    svc, proxy = _service(catalogue=None)
    _run(_say(svc, HOSTED))
    assert "What Dargle holds" not in proxy.payloads[0]["system"]


def test_an_empty_block_leaves_no_dangling_section():
    svc, proxy = _service(_FakeCatalogue(block=""))
    _run(_say(svc, HOSTED))
    assert "What Dargle holds" not in proxy.payloads[0]["system"]


def test_the_budget_knows_the_block_is_there():
    # A prefix the arithmetic does not account for is how a window overflows
    # in production and nowhere else.
    svc, _ = _service(_FakeCatalogue())
    with_block = svc._budget_for(LOCAL, extra_prefix_chars=len(HOLDINGS))
    without = svc._budget_for(LOCAL)
    assert with_block.history_chars < without.history_chars


def test_it_sits_with_the_stable_sections():
    # llama.cpp reuses the longest common prefix; the holdings behind the
    # per-turn context would force a full re-prefill every message.
    svc, proxy = _service(_FakeCatalogue())
    _run(_say(svc, HOSTED))
    _run(_say(svc, HOSTED))          # second turn: history now exists
    system = proxy.payloads[1]["system"]
    assert system.index(HOLDINGS) < system.index("Previous conversation:")
