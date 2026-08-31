"""The tool surface under load, concurrency and hostility.

This is the assistant's most privileged interface: it reads the graph as
the asking user, edits their documents, writes their Studio projects and
audits in their name. A vulnerability here is a disaster; a failure is a
user watching an answer die. So the battery does not stop at "each tool
works once": it runs DOZENS of calls concurrently and asserts the
results are consistent, the shared accounting balances, privileges hold
under pressure, hung tools cannot freeze a turn, and the whole surface
stays fast.

Grown from a production failure: two parallel tool calls corrupted the
shared DB session and killed the stream silently (2026-08-28). The lock
that fixed it made per-call timeouts mandatory — one hung call would now
freeze the whole turn — and this file is where both properties are held.
"""
from __future__ import annotations

import asyncio
import json
import time

import pytest

from src.assistant import calc_tools, tool_runtime
from src.assistant.tool_runtime import ToolRuntime, ToolTurnContext


def _run(coro_fn, *args):
    """Run an async callable on a fresh loop.

    A callable, not a coroutine/gather built outside the loop: futures
    bind to the loop that exists when they are CREATED, and a gather
    assembled at test scope binds its children to the previous test's
    closed loop ("future belongs to a different loop").
    """
    return asyncio.run(coro_fn(*args))


def _dispatch(rt, name, args, *, studio=None, doc=None, allowed=None,
              budget=None, traced=None, client=None):
    return rt.dispatch(
        client, name, args, studio=studio, doc=doc,
        nav_routes=[], pending_nav=[],
        budget=budget if budget is not None else [1_000_000],
        name_cache={}, traced=traced, allowed=allowed,
    )


# ── consistency: dozens of concurrent calls, every result its own ────────

def test_forty_concurrent_calculator_calls_each_get_their_own_answer():
    """No cross-talk: call i must come back with call i's arithmetic.

    The calculator shares nothing between calls by design; this asserts
    the DISPATCH layer keeps it that way when forty closures run at once
    — ids, traces and results must not bleed across concurrent calls.
    """
    rt = ToolRuntime()
    traced: list = []

    async def one(i):
        out, _ = await _dispatch(
            rt, calc_tools.CALC_TOOL_NAME,
            {"expression": f"{i} * 1000 + {i}"}, traced=traced)
        return i, json.loads(out)

    async def battery():
        return await asyncio.gather(*(one(i) for i in range(40)))

    for i, result in _run(battery):
        assert result["result"] == i * 1000 + i, \
            f"call {i} got someone else's answer: {result}"
    assert len(traced) == 40, "every call traces exactly once"
    ids = [t.get("call_id") for t in traced]
    assert len(set(ids)) == 40 and None not in ids, "call ids must be unique"


def test_shared_budget_stays_consistent_under_concurrency():
    """The per-turn budget list is shared mutable state; forty concurrent
    spenders must leave it balanced — never negative, and spent exactly
    what the results consumed."""
    rt = ToolRuntime()
    start = 100_000
    budget = [start]

    async def one(i):
        out, _ = await _dispatch(
            rt, calc_tools.CALC_TOOL_NAME,
            {"expression": f"{i} + 1"}, budget=budget)
        return len(out)

    async def battery():
        return await asyncio.gather(*(one(i) for i in range(40)))

    _run(battery)
    assert budget[0] >= 0, "budget must never go negative"
    assert budget[0] < start, "forty results must have spent something"


def test_concurrent_runaway_calculations_all_terminate():
    """Two bombs at once must both be stopped by their own budgets —
    the sandbox's step/wall-clock bounds are per-evaluation, not global,
    so one bomb cannot starve or extend another."""
    rt = ToolRuntime()
    bomb = "t = 0\nfor i in range(10000):\n    for j in range(10000):\n        t += 1\nt"

    async def one():
        out, _ = await _dispatch(rt, calc_tools.CALC_TOOL_NAME,
                                 {"expression": bomb})
        return json.loads(out)

    async def battery():
        return await asyncio.gather(one(), one(), one())

    started = time.monotonic()
    results = _run(battery)
    assert time.monotonic() - started < 15
    for r in results:
        assert "error" in r and ("steps" in r["error"] or "seconds" in r["error"])


# ── privilege enforcement under pressure ─────────────────────────────────

def test_the_anonymous_allowlist_holds_for_every_one_of_forty_calls():
    """The allowlist is the guarantee, not the prompt. Under a fan-out it
    must hold for call 40 exactly as for call 1 — a single leak is a
    signed-out visitor running a privileged tool."""
    rt = ToolRuntime()
    allowed = frozenset({"navigate"})

    async def one(i):
        out, _ = await _dispatch(
            rt, calc_tools.CALC_TOOL_NAME, {"expression": f"{i}"},
            allowed=allowed)
        return json.loads(out)

    async def battery():
        return await asyncio.gather(*(one(i) for i in range(40)))

    results = _run(battery)
    for r in results:
        assert "not available to signed-out visitors" in r.get("error", ""), r


# ── hung tools cannot freeze a turn ─────────────────────────────────────

def test_a_hung_tool_times_out_into_an_error_the_model_can_read(monkeypatch):
    """Since dispatch serializes on the turn lock, one hung call would
    freeze every later tool, the persistence, the stream. The ceiling
    turns a hang into an error result within TOOL_CALL_TIMEOUT_S."""
    rt = ToolRuntime()
    monkeypatch.setattr(tool_runtime, "TOOL_CALL_TIMEOUT_S", 0.2)

    class _HungStudio:
        async def execute(self, *_a, **_k):
            await asyncio.sleep(60)

    async def call():
        started = time.monotonic()
        out, raw = await _dispatch(
            rt, "mcp__gmr__studio_list_projects", {}, studio=_HungStudio())
        return json.loads(out), raw, time.monotonic() - started

    result, raw, elapsed = _run(call)
    assert elapsed < 2, "the hang must be cut, not waited out"
    assert "timed out" in result["error"]
    assert raw == 0


def test_a_hung_tool_releases_the_turn_for_the_next_call(monkeypatch):
    """After a timeout, the next tool call proceeds normally — the hang
    consumed its ceiling and nothing else."""
    rt = ToolRuntime()
    monkeypatch.setattr(tool_runtime, "TOOL_CALL_TIMEOUT_S", 0.2)

    class _HungStudio:
        async def execute(self, *_a, **_k):
            await asyncio.sleep(60)

    async def battery():
        lock = asyncio.Lock()

        async def locked(coro):
            async with lock:
                return await coro

        hung = locked(_dispatch(rt, "mcp__gmr__studio_list_projects", {},
                                studio=_HungStudio()))
        calc = locked(_dispatch(rt, calc_tools.CALC_TOOL_NAME,
                                {"expression": "2 + 2"}))
        (h_out, _), (c_out, _) = await asyncio.gather(hung, calc)
        return json.loads(h_out), json.loads(c_out)

    hung_result, calc_result = _run(battery)
    assert "timed out" in hung_result["error"]
    assert calc_result["result"] == 4


# ── performance: generous bounds, but bounds ─────────────────────────────

def test_dispatch_overhead_stays_negligible():
    """A thousand trivial dispatches in well under CI noise. If this ever
    trips, the per-call plumbing (uuid, audit scope, trace, timeout wrap)
    has grown a real cost and every turn pays it."""
    rt = ToolRuntime()

    async def battery():
        for i in range(1000):
            await _dispatch(rt, calc_tools.CALC_TOOL_NAME,
                            {"expression": "1 + 1"})

    started = time.monotonic()
    _run(battery)
    assert time.monotonic() - started < 10


def test_serialization_under_the_turn_lock_is_not_a_bottleneck():
    """Fifty locked calls of ~1ms of work must finish in far less than a
    second of overhead — the lock trades fan-out for correctness, and
    this pins that the trade stays cheap."""
    rt = ToolRuntime()

    async def battery():
        lock = asyncio.Lock()

        async def one(i):
            async with lock:
                out, _ = await _dispatch(rt, calc_tools.CALC_TOOL_NAME,
                                         {"expression": f"{i} * 2"})
                return json.loads(out)["result"]

        return await asyncio.gather(*(one(i) for i in range(50)))

    started = time.monotonic()
    results = _run(battery)
    assert time.monotonic() - started < 5
    assert results == [i * 2 for i in range(50)]


# ── the session-guard end to end ────────────────────────────────────────

def test_a_session_like_resource_is_never_entered_concurrently():
    """The production failure, as a test: a resource that BREAKS on
    concurrent entry (like an AsyncSession) survives a 24-call fan-out
    when every dispatch takes the turn lock."""
    rt = ToolRuntime()

    class _GuardedStudio:
        def __init__(self):
            self.inside = False
            self.calls = 0

        async def execute(self, name, _args, **_kw):
            if self.inside:
                raise RuntimeError(
                    "IllegalStateChange: concurrent session use")
            self.inside = True
            try:
                await asyncio.sleep(0.005)
                self.calls += 1
                return json.dumps({"ok": name})
            finally:
                self.inside = False

    studio = _GuardedStudio()

    async def battery():
        lock = asyncio.Lock()

        async def one():
            async with lock:
                return await _dispatch(
                    rt, "mcp__gmr__studio_list_projects", {}, studio=studio)

        return await asyncio.gather(*(one() for _ in range(24)))

    outs = _run(battery)
    assert studio.calls == 24
    for out, _ in outs:
        assert json.loads(out).get("ok"), out




# ── what the repeat signature is keyed on ───────────────────────────────
#
# The degenerate-loop guard — a third byte-identical call is answered by
# the dispatcher instead of running the tool again, after a 1.7B looped
# calculate(len('…')) twelve times and timed out the staging gate (attest
# 28604) — is covered for the calculator in test_calculator.py.
#
# What is not covered there is what makes two calls "identical", and every
# one of these is a way for the guard to misfire on a turn that is not
# looping at all. A guard that refuses honest work is worse than no guard:
# it costs the user a call that would have worked, and says nothing a
# reader could act on.

def _unknown_tools_runtime():
    """A runtime whose unknown names resolve locally to "Unknown tool".

    These are about the repeat signature, not tool resolution, and the
    generated-tool spec fetch would otherwise need a live fontem-api to
    say "I have never heard of that".
    """
    rt = ToolRuntime()

    async def no_generated(_client):
        return []

    rt._get_generated_tools = no_generated  # pylint: disable=protected-access
    return rt


def _turn(rt, traced):
    """Dispatch into one turn's history, on this test's own loop."""
    def call(name, args):
        return _run(lambda: _dispatch(rt, name, args, traced=traced))
    return call


def test_the_same_arguments_to_a_different_tool_are_not_a_repeat():
    """The signature is (tool, args), not args alone.

    Two tools asked the same question are two different questions —
    resolving an id and then investigating it carry the same argument dict,
    and keying on args alone would refuse the second.
    """
    call = _turn(_unknown_tools_runtime(), [])
    args = {"query": "Metro Mondego"}

    call("mcp__gmr__unknown_a", args)
    call("mcp__gmr__unknown_a", args)
    out, _ = call("mcp__gmr__unknown_b", args)

    assert "already called" not in out, out


def test_argument_order_does_not_disguise_a_repeat():
    """Sorted, so the loop cannot be walked around by accident.

    Nothing makes a model emit its keys in a stable order, so an unsorted
    signature would let the exact loop this guard exists for run free.
    """
    call = _turn(_unknown_tools_runtime(), [])

    call("mcp__gmr__unknown_a", {"from_id": "a", "to_id": "b"})
    call("mcp__gmr__unknown_a", {"to_id": "b", "from_id": "a"})
    out, _ = call("mcp__gmr__unknown_a", {"to_id": "b", "from_id": "a"})

    assert "already called with exactly these arguments" in json.loads(out)["error"]


def test_a_turn_with_no_history_never_refuses():
    """`traced=None` means nobody is recording this turn — there is no
    history to call a repeat, and the guard must not invent one."""
    rt = ToolRuntime()
    args = {"expression": "3 + 3"}

    for _ in range(4):
        out, _ = _run(lambda: _dispatch(
            rt, calc_tools.CALC_TOOL_NAME, args, traced=None))
        assert "already called" not in out, out
