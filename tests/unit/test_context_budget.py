"""The continuity window is a share of the model's context, not a constant.

Every budget in the assistant used to be sized for the smallest model served,
so a 1M-context model and a 32k one were handed the same ~7,700 tokens of
conversation — 23% of the small one's window and 0.7% of the large one's.

The arithmetic is deliberately pessimistic: a flat 3 characters per token
against text that is sometimes prose, sometimes JSON, sometimes neither. Being
wrong low wastes context; being wrong high overflows the window mid-turn, and
only one of those is visible to the person asking the question.
"""
from __future__ import annotations

import pytest

from src.assistant.context_budget import CHARS_PER_TOKEN, ContextBudget, derive


PREFIX = 7_176        # measured: system prompt + tool schemas
REPLY = 4_000
FLOOR_HISTORY = 12_000
FLOOR_TOOLS = 14_000


def budget(context_tokens: int, **kw):
    return derive(
        context_tokens=context_tokens,
        fixed_prefix_chars=kw.get("prefix", PREFIX),
        reply_tokens=kw.get("reply", REPLY),
        floor_history_chars=FLOOR_HISTORY,
        floor_tool_chars=FLOOR_TOOLS,
    )


def test_the_conversion_is_pessimistic_not_average():
    """Prose is nearer 4 and the earlier estimate used it; JSON tool output
    runs closer to 3 and overflowed the window. One conservative number beats
    a correct average when the failure is asymmetric."""
    assert CHARS_PER_TOKEN == 3


def test_a_bigger_context_buys_more_history():
    small = budget(32_768)
    large = budget(1_048_576)
    assert large.history_chars > small.history_chars * 10


@pytest.mark.parametrize("context", [32_768, 131_072, 1_048_576])
def test_the_whole_turn_fits_inside_the_context(context):
    """The invariant that matters. Prefix, history, tool results and the
    model's own reply have to coexist in one window."""
    b = budget(context)
    used_tokens = (
        PREFIX // CHARS_PER_TOKEN
        + b.history_chars // CHARS_PER_TOKEN
        + b.tool_chars // CHARS_PER_TOKEN
        + REPLY
    )
    assert used_tokens <= context, f"{used_tokens} tokens into a {context} window"


@pytest.mark.parametrize("context", [32_768, 131_072, 1_048_576])
def test_no_model_gets_less_than_it_had_before(context):
    """The floors are the constants this replaced. A number smaller than
    today's is a regression dressed up as a calculation."""
    b = budget(context)
    assert b.history_chars >= FLOOR_HISTORY
    assert b.tool_chars >= FLOOR_TOOLS


def test_the_reply_is_reserved_not_borrowed():
    """A window that fits the conversation exactly and leaves nothing to answer
    with is the same failure as an overflow, arriving one step later."""
    generous = budget(131_072, reply=1_000)
    hungry = budget(131_072, reply=40_000)
    assert hungry.history_chars < generous.history_chars


def test_a_larger_prefix_leaves_less_for_the_conversation():
    lean = budget(131_072, prefix=4_000)
    fat = budget(131_072, prefix=120_000)
    assert fat.history_chars < lean.history_chars


def test_a_context_too_small_to_divide_falls_back_to_the_floors():
    """Not to zero. A turn with no history is bad; a turn that cannot be built
    at all is worse."""
    b = budget(1_024)
    assert b.history_chars == FLOOR_HISTORY
    assert b.tool_chars == FLOOR_TOOLS


def test_the_8b_gets_several_times_the_old_fixed_window():
    """The concrete claim: the model we serve by default was throttled roughly
    four-fold below its own context."""
    b = budget(32_768)
    assert b.history_chars > FLOOR_HISTORY * 3


def test_history_and_tools_both_get_a_share():
    b = budget(131_072)
    assert b.history_chars > 0 and b.tool_chars > 0
    # history is the larger share: tool results are capped per-result
    # elsewhere and are the first thing dropped when the window tightens.
    assert b.history_chars > b.tool_chars


def test_the_budget_is_immutable():
    b = budget(32_768)
    with pytest.raises(Exception):
        b.history_chars = 1  # type: ignore[misc]


def test_it_reports_what_was_left_for_logging():
    b = budget(131_072)
    assert isinstance(b, ContextBudget)
    assert b.working_tokens > 0


# --- what the service actually does with it ---------------------------------

def test_the_service_sizes_the_window_from_the_chosen_model():
    """The wiring, not just the arithmetic.

    `_limits_for` is what turns a picker choice into a window. Before it, the
    limits were a module constant handed to the service once, so every model
    got the smallest model's window.
    """
    # pylint: disable=import-outside-toplevel,protected-access
    from src.assistant.context import TurnLimits
    from src.assistant.service import AssistantService

    service = AssistantService(
        repo=None, proxy_client=None, base_system_prompt="",
        turn_limits=TurnLimits(max_turns=20, max_chars=FLOOR_HISTORY),
        context_char_budget=8_000,
        fixed_prefix_chars=PREFIX, reply_tokens=REPLY,
    )
    small = service._limits_for("qwen3-1.7b")     # 32,768 context
    large = service._limits_for("ox-alpha")       # 1,048,576 context
    assert large.max_chars > small.max_chars * 10
    assert small.max_chars >= FLOOR_HISTORY


def test_an_unknown_model_still_gets_a_usable_window():
    """resolve() falls back to the default model, so a stale preference does
    not produce a turn with no history at all."""
    # pylint: disable=import-outside-toplevel,protected-access
    from src.assistant.context import TurnLimits
    from src.assistant.service import AssistantService

    service = AssistantService(
        repo=None, proxy_client=None, base_system_prompt="",
        turn_limits=TurnLimits(max_turns=20, max_chars=FLOOR_HISTORY),
        context_char_budget=8_000,
        fixed_prefix_chars=PREFIX, reply_tokens=REPLY,
    )
    assert service._limits_for("retired-model").max_chars >= FLOOR_HISTORY


def test_the_prefix_is_measured_from_the_shipped_prompt_and_schemas():
    """Hardcoding it means a prompt edit silently steals from the window."""
    # pylint: disable=import-outside-toplevel
    from src.api.di import _fixed_prefix_chars, _DEFAULT_SYSTEM_PROMPT

    measured = _fixed_prefix_chars()
    assert measured > len(_DEFAULT_SYSTEM_PROMPT), "tool schemas are not counted"
    assert measured > 1_000
