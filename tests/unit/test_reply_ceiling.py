"""The served turn carries a reply budget we chose.

The serving path set no `max_tokens` at all, so whatever the provider
defaulted to applied. pydantic-ai reports a reply truncated that way as

    Model token limit (provider default) exceeded before any response was
    generated

— a message that names no number precisely because we sent none. An
author hit it mid-article on 2026-09-02.

"(provider default)" is the tell: `last_max_tokens` was unset. These
tests pin that a number is now sent, that it is generous, and that it
never asks a window for more than it has left.
"""
from __future__ import annotations

from src.assistant import local_models
from src.assistant.pydantic_ai_client import _reply_ceiling


def _payload(model_id: str, history_chars: int = 0) -> dict:
    p = {"local_model_id": model_id}
    if history_chars:
        p["history"] = [{"content": "x" * history_chars}]
    return p


class TestTheReplyCeiling:

    def test_a_reasoning_model_gets_room_for_thinking_and_an_answer(self):
        # MiniMax spends its budget thinking before the first answer token,
        # so the same ceiling buys far less answer than on a direct model.
        assert _reply_ceiling(_payload("minimax-m3"), "sys", "hi") == 32_000

    def test_a_direct_answerer_gets_the_standard_budget(self):
        assert _reply_ceiling(_payload("qwen3-4b"), "sys", "hi") == 8_000

    def test_it_never_asks_for_more_than_the_window_has_left(self):
        # 32k model, ~80k characters of thread: a flat 8k ceiling would ask
        # for tokens that do not exist, and llama.cpp answers that with a
        # 400 rather than a shorter reply.
        room = _reply_ceiling(_payload("qwen3-4b", 80_000), "s", "m")
        assert room < 8_000
        assert room > 0

    def test_a_million_token_window_is_not_shrunk_by_a_long_thread(self):
        # The reasoning models have room to spare; the cap should stay the
        # model's own budget rather than tracking the prompt down.
        assert _reply_ceiling(_payload("minimax-m3", 2_000_000), "s", "m") == 32_000

    def test_it_never_returns_something_unusable(self):
        """Even an absurd prompt leaves a floor, so the turn fails on the
        provider's terms rather than on a max_tokens of zero or negative."""
        assert _reply_ceiling(_payload("qwen3-1.7b", 500_000), "s", "m") >= 512

    def test_every_offered_model_declares_a_serving_budget(self):
        for m in local_models.LOCAL_MODELS:
            assert m.reply_max_tokens >= 8_000, m.id
            # The eval budget is deliberately smaller — short leash for a
            # cheap comparable run — and must not be mistaken for this one.
            assert m.reply_max_tokens > m.eval_max_tokens, m.id

    def test_reasoning_models_get_more_than_direct_ones(self):
        reasoning = [m for m in local_models.LOCAL_MODELS if m.reasoning]
        direct = [m for m in local_models.LOCAL_MODELS if not m.reasoning]
        assert reasoning and direct
        assert min(m.reply_max_tokens for m in reasoning) > \
            max(m.reply_max_tokens for m in direct)
