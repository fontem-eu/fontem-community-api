"""The calculator: exact, bounded, and never a Python eval.

The witness property is the point — a number computed here is a tool
result the grounding check can trace — so the tests also pin that the
result string reaches the model through the normal dispatch path.
"""
# pylint: disable=missing-function-docstring,protected-access
from __future__ import annotations

import ast
import asyncio
import inspect
import json
import time
from unittest.mock import MagicMock

import pytest

from src.assistant import calc_tools
from src.assistant.tool_runtime import ToolRuntime


def _calc(expression, values=None):
    return json.loads(calc_tools.execute(
        {"expression": expression, **({"values": values} if values else {})}))


# ── arithmetic ────────────────────────────────────────────────

def test_the_basics_are_exact():
    assert _calc("2 + 3 * 4")["result"] == 14
    assert _calc("(12874355.33 - 9310000) / 9310000 * 100")["result"] == \
        pytest.approx(38.285, abs=0.001)
    assert _calc("2 ** 10")["result"] == 1024
    assert _calc("-7 // 2")["result"] == -4


def test_bound_values_and_aggregates():
    out = _calc("sum(v) / len(v)", {"v": [1, 2, 3, 4]})
    assert out["result"] == 2.5
    assert _calc("avg([10, 20])")["result"] == 15
    assert _calc("round(a / b, 2)", {"a": 1, "b": 3})["result"] == 0.33
    assert _calc("max(v) - min(v)", {"v": [3, 9, 4]})["result"] == 6


def test_division_by_zero_is_an_answer():
    assert _calc("1 / 0")["error"] == "division by zero"


def test_an_unknown_name_names_the_fix():
    out = _calc("a + 1")
    assert "unknown name 'a'" in out["error"]
    assert "values" in out["error"]


def test_a_list_result_asks_for_an_aggregate():
    assert "aggregate" in _calc("[1, 2]")["error"]


# ── the whitelist is a wall ───────────────────────────────────

def test_calls_outside_the_whitelist_are_refused():
    for expr in ("__import__('os')", "open('/etc/passwd')", "exec('x')",
                 "(1).__class__"):
        out = _calc(expr)
        assert "error" in out, expr


def test_attributes_strings_subscripts_and_lambdas_are_not_syntax():
    for expr in ("a.b", "'abc'", "f'{1}'", "v[0]", "lambda: 1",
                 "import os", "while 1 < 2: 1", "x := 1"):
        out = _calc(expr, {"v": [1]})
        assert "error" in out, expr


def test_pathological_exponents_are_bounded():
    assert "bounded" in _calc("10 ** 10 ** 10")["error"]


def test_booleans_are_not_numbers():
    assert "error" in _calc("x + 1", {"x": True})


# ── the witness property ──────────────────────────────────────

def test_the_result_reaches_the_model_through_dispatch():
    # The grounding check reads tool results as evidence; this is the string
    # it will see.
    rt = ToolRuntime(gmr_api_url="http://fontem-api")
    loop = asyncio.new_event_loop()
    try:
        out, _ = loop.run_until_complete(rt.dispatch(
            MagicMock(), calc_tools.CALC_TOOL_NAME,
            {"expression": "935000 + 986546"},
            studio=None, nav_routes=[], pending_nav=[],
            budget=[14_000], name_cache={}, traced=[],
        ))
    finally:
        loop.close()
    assert json.loads(out)["result"] == 1921546


def test_the_module_never_calls_eval_exec_or_compile():
    # Checked structurally, not by grepping text: docstrings may mention
    # eval; the code must never call it.
    tree = ast.parse(inspect.getsource(calc_tools))
    calls = [n.func.id for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    assert not {"eval", "exec", "compile"} & set(calls)


# ── the Python-subset script form ─────────────────────────────

def test_the_exact_shapes_minimax_sent_now_work():
    # Replayed verbatim from evals/results/2026-08-27-parity-minimax-*.json:
    # numbers as strings, the list wrapped in {"item": [...]} by the model's
    # serializer. The tool refused these twice per prompt and the model
    # burned rounds falling back — correct intent, lost on a technicality.
    out = _calc("sum(v)", {"v": {"item": ["30862249.55", "24141817.03",
                                          "342610.0", "76275.16",
                                          "57991.19"]}})
    assert out["result"] == pytest.approx(55480942.93)


def test_numeric_strings_are_coerced_and_junk_is_not():
    assert _calc("a + b", {"a": "10.5", "b": "2"})["result"] == 12.5
    assert "not a number" in _calc("a + 1", {"a": "12,5"})["error"]


def test_a_script_returns_its_last_expression():
    out = _calc("total = 30862249.55 + 24141817.03\n"
                "delta = total - 50000000\n"
                "delta / 50000000 * 100")
    assert out["result"] == pytest.approx(10.008, abs=0.001)


def test_assigning_result_also_works():
    assert _calc("result = mean([1, 2, 3, 6])")["result"] == 3.0


def test_statistics_functions():
    v = {"v": [2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]}
    assert _calc("pstdev(v)", v)["result"] == pytest.approx(2.0)
    assert _calc("median(v)", v)["result"] == 4.5
    assert _calc("round(sqrt(2), 3)")["result"] == 1.414
    assert "error" in _calc("stdev([1])")  # needs two points, said plainly


def test_comprehensions_and_logic():
    out = _calc("big = [x for x in v if x > 1000000]\n"
                "sum(big) / sum(v) * 100",
                {"v": [30862249.55, 24141817.03, 342610.0, 76275.16]})
    assert out["result"] == pytest.approx(99.245, abs=0.001)
    assert _calc("sum(v) > 100", {"v": [60, 50]})["result"] is True
    out = _calc("total = 0\n"
                "for x in v:\n"
                "    if x > 10:\n"
                "        total += x\n"
                "result = total", {"v": [5.0, 20.0, 30.0]})
    assert out["result"] == 50.0


def test_scripts_are_capped_at_six_lines():
    assert "6 lines" in _calc("\n".join(f"x{i} = {i}" for i in range(7))
                              + "\nx0")["error"]


def test_runaway_loops_hit_the_step_budget_not_the_server():
    started = time.monotonic()
    out = _calc("t = 0\n"
                "for i in range(10000):\n"
                "    for j in range(10000):\n"
                "        t += 1\n"
                "t")
    assert "steps" in out["error"] or "seconds" in out["error"]
    assert time.monotonic() - started < 5


def test_memory_bombs_are_refused():
    # Repeated squaring turns an int into gigabytes without a single big
    # literal; the width guard stops it while it is still kilobytes.
    out = _calc("x = 2 ** 1000\n"
                "for i in range(100):\n"
                "    x = x * x\n"
                "x")
    assert "too large" in out["error"]
    # A comprehension fanning out past the list bound is stopped mid-build.
    out = _calc("[0 for i in range(10000) for j in range(10)]")
    assert "bounded" in out["error"]


def test_the_wall_clock_can_actually_fire(monkeypatch):
    """The seconds bound is a real branch, not decoration.

    `test_runaway_loops_hit_the_step_budget_not_the_server` accepts either
    error, because for a tight loop the step budget wins the race — which
    means nothing there would notice if the deadline check stopped working.
    The wall clock is what bounds a calculation that is slow per step
    rather than long in steps, and since 2026-08-28 every DB-touching
    dispatch serializes on the turn lock, so a calculation that runs away
    on the clock holds up the whole turn.
    """
    monkeypatch.setattr(calc_tools, "_MAX_SECONDS", -1.0)
    out = _calc("t = 0\nfor i in range(1000):\n    t += 1\nt")
    assert "seconds" in out["error"], out


def test_the_step_budget_leaves_room_for_the_clock_to_be_checked():
    """The deadline is only read every 512 ticks, so a step budget below
    that would make the wall clock unreachable — silently, with no error
    and no failing test anywhere else. Tuning _MAX_OPS down is exactly the
    kind of change that would do it."""
    assert calc_tools._MAX_OPS > 512, (
        "the wall-clock check is dead code unless a calculation can reach "
        "512 ticks before the step budget stops it"
    )


def test_range_is_bounded():
    assert _calc("sum(range(1, 5))")["result"] == 10
    assert "bounded" in _calc("len(range(100000))")["error"]


# ── the degenerate-loop guard ─────────────────────────────────

def _dispatch_n(rt, loop, traced, expression, times):
    outs = []
    for _ in range(times):
        out, _len = loop.run_until_complete(rt.dispatch(
            MagicMock(), calc_tools.CALC_TOOL_NAME,
            {"expression": expression},
            studio=None, nav_routes=[], pending_nav=[],
            budget=[14_000], name_cache={}, traced=traced,
        ))
        outs.append(json.loads(out))
    return outs


def test_the_third_identical_call_is_refused_with_a_redirect():
    # A 1.7B looped calculate(len('…')) twelve times at ~15s of inference
    # each and timed out the staging gate (attest 28604). Identical
    # arguments cannot learn anything new; the third call answers from the
    # runtime with instructions instead of burning another round.
    rt = ToolRuntime(gmr_api_url="http://fontem-api")
    loop = asyncio.new_event_loop()
    traced = []
    try:
        outs = _dispatch_n(rt, loop, traced, "1 + 1", 4)
    finally:
        loop.close()
    assert outs[0]["result"] == 2
    assert outs[1]["result"] == 2
    assert "already called" in outs[2]["error"]
    assert "answer" in outs[2]["hint"]
    assert "already called" in outs[3]["error"]


def test_new_arguments_always_run_however_many_there_are():
    # The guard is cycle detection, not an investigation cap.
    rt = ToolRuntime(gmr_api_url="http://fontem-api")
    loop = asyncio.new_event_loop()
    traced = []
    try:
        outs = [
            _dispatch_n(rt, loop, traced, f"{i} + 1", 1)[0]
            for i in range(12)
        ]
    finally:
        loop.close()
    assert [o["result"] for o in outs] == [i + 1 for i in range(12)]


def test_the_string_rejection_says_what_to_do_instead():
    # "only numbers are allowed" alone is what the 1.7B retried against.
    out = _calc("len('Apple Inc. (AAPL)') * 100")
    assert "only numbers are allowed" in out["error"]
    assert "do not retry" in out["error"]
