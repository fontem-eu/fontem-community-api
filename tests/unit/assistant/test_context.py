"""Tests for the assistant context builder.

The context builder is a pure function that takes a caller-provided
context blob and a conversation history and produces the final system
prompt + history slice sent to Claude. It is the only place where
budget management lives.
"""
# pylint: disable=missing-class-docstring,missing-function-docstring,protected-access,unused-import,too-few-public-methods
from __future__ import annotations

import pytest

from src.assistant.context import (
    TurnLimits,
    Turn,
    budget_context_block,
    truncate_history,
    build_system_prompt,
)


# ── budget_context_block ───────────────────────────────────────

class TestBudgetContextBlock:
    def test_passes_short_context_through_unchanged(self):
        block = "Title: X\n\nSome section content."
        out = budget_context_block(block, char_budget=8000)
        assert out == block

    def test_truncates_oversized_context_with_marker(self):
        block = "a" * 10_000
        out = budget_context_block(block, char_budget=1000)
        assert len(out) <= 1000 + 200  # truncation marker adds a small suffix
        assert "truncated" in out
        assert out.startswith("a")

    def test_empty_block_returns_empty(self):
        assert budget_context_block("", char_budget=8000) == ""

    def test_none_block_returns_empty(self):
        assert budget_context_block(None, char_budget=8000) == ""

    def test_respects_utf8_boundaries(self):
        # Multi-byte chars: don't cut in the middle of a codepoint
        block = "é" * 5000
        out = budget_context_block(block, char_budget=100)
        assert len(out) <= 300  # chars are counted, marker adds a bit
        # No mojibake: the truncated prefix still encodes cleanly
        out.encode("utf-8")


# ── truncate_history ───────────────────────────────────────────

def _turn(role: str, content: str) -> Turn:
    return Turn(role=role, content=content)


class TestTruncateHistory:
    def test_empty_history(self):
        assert truncate_history([], TurnLimits()) == []

    def test_keeps_all_when_under_limits(self):
        history = [
            _turn("user", "hi"),
            _turn("assistant", "hello"),
            _turn("user", "thanks"),
        ]
        out = truncate_history(history, TurnLimits(max_turns=20, max_chars=12000))
        assert out == history

    def test_drops_oldest_when_over_turn_limit(self):
        history = [_turn("user", f"msg {i}") for i in range(30)]
        out = truncate_history(history, TurnLimits(max_turns=10, max_chars=99999))
        assert len(out) == 10
        assert out[0].content == "msg 20"
        assert out[-1].content == "msg 29"

    def test_drops_oldest_when_over_char_limit(self):
        history = [
            _turn("user", "x" * 3000),
            _turn("assistant", "y" * 3000),
            _turn("user", "z" * 3000),
            _turn("assistant", "latest"),
        ]
        out = truncate_history(history, TurnLimits(max_turns=100, max_chars=7000))
        # total chars under the budget: "latest" + "z"*3000 + "y"*3000 = 6006 chars
        assert out[-1].content == "latest"
        assert sum(len(t.content) for t in out) <= 7000

    def test_single_giant_turn_is_kept_even_if_over_budget(self):
        history = [_turn("user", "x" * 20_000)]
        out = truncate_history(history, TurnLimits(max_chars=1000))
        # Never return an empty history when the caller has a message to answer
        assert len(out) == 1

    def test_preserves_relative_order(self):
        history = [_turn("user", f"q{i}") for i in range(5)]
        out = truncate_history(history, TurnLimits(max_turns=3))
        assert [t.content for t in out] == ["q2", "q3", "q4"]


# ── build_system_prompt ────────────────────────────────────────

BASE_PROMPT = "You are a research assistant."


class TestBuildSystemPrompt:
    def test_base_prompt_only(self):
        out = build_system_prompt(BASE_PROMPT, context_block="", history=[])
        assert out.strip() == BASE_PROMPT.strip()

    def test_includes_context_block_when_present(self):
        out = build_system_prompt(
            BASE_PROMPT,
            context_block="Report: Siemens investigation",
            history=[],
        )
        assert BASE_PROMPT in out
        assert "Siemens investigation" in out

    def test_includes_history_turns_in_order(self):
        history = [
            _turn("user", "Who owns Siemens?"),
            _turn("assistant", "Siemens AG is publicly traded."),
            _turn("user", "Recent contracts?"),
            _turn("assistant", "I found 42 contracts."),
        ]
        out = build_system_prompt(BASE_PROMPT, context_block="", history=history)
        # History renders as turns that Claude can read as prior context
        assert "Who owns Siemens?" in out
        assert "publicly traded" in out
        assert "42 contracts" in out
        # Order preserved — owns appears before contracts
        assert out.index("Who owns") < out.index("Recent contracts")

    def test_context_block_and_history_both_present(self):
        out = build_system_prompt(
            BASE_PROMPT,
            context_block="Report: Blah",
            history=[_turn("user", "Earlier question")],
        )
        assert "Blah" in out
        assert "Earlier question" in out
        assert BASE_PROMPT in out

    def test_never_leaks_raw_empty_sections(self):
        # No phantom "Previous conversation:" / "Current context:" headers
        # when the inputs are empty
        out = build_system_prompt(BASE_PROMPT, context_block="", history=[])
        assert "Previous conversation" not in out
        assert "Current context" not in out
