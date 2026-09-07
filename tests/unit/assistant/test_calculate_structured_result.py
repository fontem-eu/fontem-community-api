"""A calculation may answer with several named figures at once.

From the story-loop runs: a data story ends in a handful of summary
numbers, and the calculator answered one per call. Run 4 spent three calls
discovering that `result = {...}` was refused at the parser; run 5, with a
clearer refusal, still spent four calls to report three figures (16-19).

The refusal was never protecting anything about the numbers — it was the
scalar shape of the return value. Numbers, lists and mappings of them are
all safe to return; the guards that matter (the AST whitelist, the size
caps, finiteness) are unchanged.
"""
# pylint: disable=missing-function-docstring
from __future__ import annotations

import json

from src.assistant.calc_tools import execute


def _run(expr, **kw):
    return json.loads(execute({"expression": expr, **kw}))


def test_the_call_run_5_wanted_now_works_in_one():
    out = _run("before = 55004067 + 57991\n"
               "after = 418885\n"
               "result = {'before': before, 'after': after, "
               "'ratio': round(before / after, 1)}")
    assert out["result"] == {"before": 55062058, "after": 418885, "ratio": 131.4}


def test_a_list_result_is_returned_rather_than_refused():
    assert _run("result = [1, 2, 3]")["result"] == [1, 2, 3]


def test_a_plain_number_still_works():
    assert _run("2 + 2")["result"] == 4


def test_keys_are_labels_not_operands():
    # `_eval_constant` refuses strings because a 1.7B looped on
    # len('some text') until the staging gate timed out. Allowing a string
    # KEY does not reopen that: keys are read off the AST, never evaluated.
    err = _run("result = {'label': 'Latvia'}")["error"]
    assert "only numbers are allowed" in err
    assert "do not retry" in err


def test_a_non_constant_key_is_refused():
    assert "keys must be plain names" in _run("x = 1\nresult = {x: 2}")["error"]


def test_arithmetic_errors_still_surface_from_inside_a_mapping():
    assert _run("result = {'a': 1 / 0}")["error"] == "division by zero"


def test_a_non_finite_value_is_caught_however_deep():
    assert "not a finite number" in _run("result = {'a': [1e308 * 10]}")["error"]


def test_the_mapping_is_size_capped():
    err = _run("result = {'a': [i for i in range(20000)]}").get("error", "")
    assert "bounded" in err


def test_the_unsupported_message_no_longer_promises_one_number_per_call():
    err = _run("result = {1, 2}")["error"]          # a set, still unsupported
    assert "Set" in err
    assert "single number" not in err
    assert "'before'" in err, "point at the shape that does work"
