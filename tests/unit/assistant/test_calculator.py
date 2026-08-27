"""The calculator: exact, bounded, and never a Python eval.

The witness property is the point — a number computed here is a tool
result the grounding check can trace — so the tests also pin that the
result string reaches the model through the normal dispatch path.
"""
# pylint: disable=missing-function-docstring
from __future__ import annotations

import ast
import asyncio
import inspect
import json
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


def test_attributes_comprehensions_and_strings_are_not_syntax():
    for expr in ("a.b", "[x for x in v]", "'abc'", "f'{1}'"):
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
