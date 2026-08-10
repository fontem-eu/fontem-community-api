"""Tool results must fit the context the model actually has.

A tool result is unbounded. An entity search on a well-connected company
produced ~90k tokens during the model evaluation, every result is appended to
the conversation, and the conversation is re-sent whole on the next round —
so one fat result puts the request over the server's per-slot context and
llama.cpp rejects it outright:

    request (36458 tokens) exceeds the available context size (16384 tokens)

That is not a degraded answer. The stream emits `event: error`, no proposal
card ever renders, and the user sees an assistant that stopped working. It
reached staging as an intermittent e2e failure, which is the mildest form
this bug takes; the same overflow with a longer conversation is permanent.

The eval harness capped these from its first run. Production did not — the
measurement rig was safe while the product was not, which is exactly why
nothing surfaced until a browser drove it.
"""
from src.assistant.tool_budget import (
    MAX_TOOL_RESULT_CHARS,
    MAX_TOOL_RESULT_CHARS_PER_TURN,
    cap_tool_result,
)


def test_small_result_is_passed_through_untouched():
    """The cap must be invisible to the results that do not need it."""
    out, left = cap_tool_result("small", 1000)
    assert out == "small"
    assert left == 1000 - len("small")


def test_oversized_result_is_cut_to_the_per_result_ceiling():
    huge = "x" * 200_000
    out, _ = cap_tool_result(huge, MAX_TOOL_RESULT_CHARS_PER_TURN)
    assert len(out) < MAX_TOOL_RESULT_CHARS * 1.1
    assert out.startswith("x" * 100)


def test_truncation_is_announced_to_the_model():
    """A silent cut is the dangerous failure, not the long result.

    Truncated silently, the model reports the first 8k characters as the
    whole record — on a transparency platform a confident partial count is
    worse than an explicit gap, because nothing downstream can tell the
    difference.
    """
    out, _ = cap_tool_result("y" * 50_000, MAX_TOOL_RESULT_CHARS_PER_TURN)
    assert "truncated" in out
    assert "50000" in out, "the model is not told how much it cannot see"


def test_ten_maximum_results_stay_within_the_turn_budget():
    """The per-result cap alone does not bound the turn.

    _MAX_TOOL_ITERATIONS is 10, so ten 8k results are reachable in one turn
    and 80k characters overflows a 16k-token context on their own. This is
    the case the per-result cap silently fails to cover.
    """
    remaining = MAX_TOOL_RESULT_CHARS_PER_TURN
    total = 0
    for _ in range(10):
        out, remaining = cap_tool_result("z" * 50_000, remaining)
        total += len(out)
    assert remaining == 0
    assert total < MAX_TOOL_RESULT_CHARS_PER_TURN * 1.2


def test_exhausted_budget_tells_the_model_to_stop_calling_tools():
    """Answer from what you have, and name the gap.

    The alternative — an empty string or an error — reads to the model as a
    tool that returned nothing, and the observed response to that is to call
    another tool, which is the opposite of what a spent budget needs.
    """
    out, left = cap_tool_result("anything", 0)
    assert left == 0
    assert "Do not call more" in out
    assert "already have" in out


def test_budget_never_goes_negative():
    remaining = 100
    for _ in range(5):
        _, remaining = cap_tool_result("q" * 10_000, remaining)
        assert remaining >= 0
