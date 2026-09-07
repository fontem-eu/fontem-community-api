"""The model is told where it is in the turn, before the cliff.

From the story-loop run of 2026-09-07T125415Z: the second turn spent all
eighteen of its calls researching — sanctions, four entity resolutions, a
third chart — and proposed NOTHING. Everything it built in that turn was
discarded.

It had no way to know. A model cannot see its own clock, and the only
existing signal is BUDGET_EXHAUSTED, which arrives after the room to act on
it is gone. This is the earlier, softer one.
"""
# pylint: disable=missing-function-docstring
from __future__ import annotations

from src.assistant import tool_budget


def test_a_fresh_turn_is_not_nagged():
    assert tool_budget.pacing_note(tool_budget.new_turn_budget(1000), 2) == ""


def test_past_the_threshold_it_says_where_the_turn_stands():
    budget = tool_budget.new_turn_budget(1000)
    budget[0] = 300
    note = tool_budget.pacing_note(budget, 12)
    assert "12 tool calls" in note
    assert "300 of 1000" in note
    # The instruction is the point: the run that motivated this ended with
    # research and no proposal.
    assert "Propose your edits" in note


def test_it_reports_only_what_is_measured():
    # No "you have N calls left": the ceiling is on output volume, not on
    # call count, and inventing a number the server does not enforce is how
    # a model stops early on a turn that had room.
    budget = tool_budget.new_turn_budget(1000)
    budget[0] = 100
    assert "calls left" not in tool_budget.pacing_note(budget, 30)


def test_an_exhausted_budget_leaves_it_to_the_hard_stop():
    budget = tool_budget.new_turn_budget(1000)
    budget[0] = 0
    assert tool_budget.pacing_note(budget, 40) == ""
    assert "Do not call more tools" in tool_budget.BUDGET_EXHAUSTED


def test_a_legacy_one_slot_budget_is_silent_rather_than_wrong():
    # Tests and any not-yet-updated caller pass [total]. Guessing a total
    # from a remaining would report a fraction of a number nobody set.
    assert tool_budget.pacing_note([500], 4) == ""
    assert tool_budget.pacing_note([], 4) == ""


def test_the_cell_still_works_as_the_cap_target():
    # tool_runtime mutates budget[0] in place; the extra slots must not
    # disturb that contract.
    budget = tool_budget.new_turn_budget(120)
    out, budget[0] = tool_budget.cap_tool_result("x" * 200, budget[0])
    # The payload is cut to the budget; the truncation marker rides on top
    # of it, so the returned string is legitimately longer than the cap.
    assert out.startswith("x" * 120)
    assert "truncated" in out
    assert budget[0] == 0, "the whole budget was consumed"
    assert budget[1] == 120, "the total slot is untouched by capping"
