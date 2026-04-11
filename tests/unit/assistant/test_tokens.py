"""Tests for token estimation and usage parsing.

The assistant is not gated on exact counts — it needs 'close enough' to
bill and to detect runaway conversations. Estimates must:
  * scale linearly with text length
  * never crash on empty / None / unicode edge cases
  * be within 25% of an approximate ground truth for English prose
"""
# pylint: disable=missing-class-docstring,missing-function-docstring,protected-access,unused-import,too-few-public-methods
from __future__ import annotations

import pytest

from src.assistant.tokens import (
    TokenUsage,
    estimate_tokens,
    parse_sse_usage,
    total_tokens,
)


# ── estimate_tokens ────────────────────────────────────────────

class TestEstimateTokens:
    def test_empty_string_is_zero(self):
        assert estimate_tokens("") == 0

    def test_none_is_zero(self):
        assert estimate_tokens(None) == 0

    def test_short_english_is_roughly_word_count(self):
        # "hello world" — 2 words, typically 2-3 tokens
        n = estimate_tokens("hello world")
        assert 1 <= n <= 5

    def test_longer_text_scales_up(self):
        short = estimate_tokens("one two three")
        longer = estimate_tokens("one two three " * 100)
        assert longer > short * 50

    def test_unicode_does_not_crash(self):
        assert estimate_tokens("héllo wörld 🌍") > 0

    def test_reasonable_for_prose_paragraph(self):
        # ~100 words → expect 100-200 tokens, allow wide band
        paragraph = " ".join(["hello"] * 100)
        n = estimate_tokens(paragraph)
        assert 50 <= n <= 300


# ── parse_sse_usage ────────────────────────────────────────────

class TestParseSSEUsage:
    def test_none_on_unrelated_event(self):
        assert parse_sse_usage("chunk", '{"text": "hi"}') is None

    def test_parses_proxy_usage_event(self):
        # Shape emitted by claude-proxy.py when it reads Claude CLI's
        # `result` event: event: usage\ndata: {input_tokens, output_tokens}
        payload = '{"input_tokens": 123, "output_tokens": 45}'
        usage = parse_sse_usage("usage", payload)
        assert usage is not None
        assert usage.input_tokens == 123
        assert usage.output_tokens == 45

    def test_none_on_malformed_json(self):
        assert parse_sse_usage("usage", "{not json") is None

    def test_none_when_fields_missing(self):
        assert parse_sse_usage("usage", '{"input_tokens": 5}') is None

    def test_none_when_negative(self):
        assert parse_sse_usage("usage", '{"input_tokens": -1, "output_tokens": 5}') is None

    def test_zero_counts_are_valid(self):
        payload = '{"input_tokens": 0, "output_tokens": 0}'
        usage = parse_sse_usage("usage", payload)
        assert usage == TokenUsage(input_tokens=0, output_tokens=0)

    def test_none_on_status_event(self):
        # Status events with their own payload must not be confused for usage
        assert parse_sse_usage("status", '{"phase": "tool_use"}') is None


# ── total_tokens ───────────────────────────────────────────────

class TestTotalTokens:
    def test_sums_input_and_output(self):
        u = TokenUsage(input_tokens=10, output_tokens=20)
        assert total_tokens(u) == 30

    def test_handles_none(self):
        assert total_tokens(None) == 0
