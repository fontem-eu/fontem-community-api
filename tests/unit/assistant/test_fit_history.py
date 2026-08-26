"""The continuity window degrades in a fixed order, cheapest loss first.

Full results, then calls-only, then dropping the oldest turns. The order
matters: a tool result is the largest row and the least useful once stale,
whereas losing the turn that produced it costs the model the knowledge that
it ran at all — and it will run it again.
"""
# pylint: disable=missing-function-docstring
from __future__ import annotations

from src.assistant.context import (
    Turn,
    TurnLimits,
    build_system_prompt,
    fit_history,
    strip_tool_results,
)


def _tool(name: str, result: str) -> Turn:
    return Turn(role="tool", content=result, name=name)


def test_nothing_is_given_up_when_it_all_fits():
    history = [Turn("user", "hi"), _tool("search", "x" * 50), Turn("assistant", "hello")]
    kept, dropped = fit_history(history, TurnLimits(max_turns=20, max_chars=10_000))
    assert kept == history
    assert dropped == []


def test_tool_results_go_before_any_turn_is_dropped():
    history = [Turn("user", "hi"), _tool("search", "x" * 500), Turn("assistant", "ok")]
    kept, dropped = fit_history(history, TurnLimits(max_turns=20, max_chars=100))

    assert dropped == [], "no turn should be lost while blanking results still fits"
    assert [t.role for t in kept] == ["user", "tool", "assistant"]
    assert kept[1].content == ""
    assert kept[1].name == "search", "the call itself must survive"


def test_turns_are_dropped_only_once_blanking_is_not_enough():
    history = [Turn("user", "u" * 200) for _ in range(10)]
    kept, dropped = fit_history(history, TurnLimits(max_turns=20, max_chars=400))

    assert dropped, "the oldest turns should fall off"
    assert len(kept) < len(history)
    assert kept == history[len(dropped):], "kept must be the tail, in order"


def test_dropped_and_kept_together_account_for_everything():
    history = [Turn("user", "u" * 300) for _ in range(8)]
    kept, dropped = fit_history(history, TurnLimits(max_turns=20, max_chars=500))
    assert len(kept) + len(dropped) == len(history)


def test_an_empty_history_is_not_a_special_case():
    assert fit_history([], TurnLimits()) == ([], [])


def test_strip_tool_results_leaves_other_roles_alone():
    history = [Turn("user", "hi"), _tool("search", "big"), Turn("assistant", "ok")]
    lean = strip_tool_results(history)
    assert [t.content for t in lean] == ["hi", "", "ok"]
    assert lean[1].name == "search"


def test_a_tool_turn_is_not_rendered_as_the_assistant_speaking():
    # It used to render as "Assistant: search_companies", which the model
    # reads as the assistant having said that string.
    prompt = build_system_prompt("base", "", [_tool("search_companies", "3 hits")])
    assert "Tool search_companies: 3 hits" in prompt
    assert "Assistant: search_companies" not in prompt


def test_a_blanked_tool_turn_still_says_the_call_happened():
    prompt = build_system_prompt("base", "", [_tool("search_companies", "")])
    assert "Tool search_companies: (called)" in prompt
