"""How much of a model's context this deployment is willing to spend.

Every budget in the assistant used to be a constant sized for the smallest
model we served. `MAX_TOOL_RESULT_CHARS_PER_TURN` says so outright — "sized
against the smallest context we serve: 16384 tokens" — and `TurnLimits`
carried a flat 12,000 characters of history. Together that is roughly 7,700
tokens of conversation handed to every model alike:

    qwen3-8b     32,768 context   23.4% used
    hosted      131,072 context    5.8% used
    Ox Alpha  1,048,576 context    0.7% used

The number needed to do better already existed. `LocalModel.context_tokens` is
declared for every model in the picker and, until this module, nothing read it:
it was display text beside a radio button.

CHARACTERS PER TOKEN

Everything here counts characters and the context window counts tokens, so
something has to convert. This module uses a flat, deliberately pessimistic
3 characters per token.

That is not the average. English prose runs nearer 4, and the earlier estimate
used it — 24,000 characters derived from a 4-char rule, which still overflowed
because "JSON tool output is punctuation-dense and runs closer to 3
chars/token". Assistant turns mix prose, JSON tool output and 23 locales'
worth of non-Latin text, where the ratio moves again.

A single conservative number is worth more than a correct average here. Being
wrong low wastes context. Being wrong high overflows the window mid-turn,
which is the failure the caller sees.
"""
from __future__ import annotations

from dataclasses import dataclass

#: Deliberately pessimistic, and applied to every kind of text. See the module
#: docstring: an average that is right most of the time still overflows on
#: JSON, and overflow is the expensive direction to be wrong in.
CHARS_PER_TOKEN = 3


@dataclass(frozen=True)
class ContextBudget:
    """What one turn may spend, in characters."""

    #: Room for the conversation so far.
    history_chars: int
    #: Room for every tool result in this turn, together.
    tool_chars: int
    #: What was left after the prefix and the reply reservation, for logging.
    working_tokens: int

    @property
    def summarise(self) -> bool:
        """Whether history that overflows needs summarising rather than dropping.

        Kept as a property so callers ask the budget rather than comparing
        numbers themselves.
        """
        return self.history_chars > 0


def derive(
    *,
    context_tokens: int,
    fixed_prefix_chars: int,
    reply_tokens: int,
    floor_history_chars: int,
    floor_tool_chars: int,
    history_share: float = 0.6,
    safety_tokens: int = 512,
) -> ContextBudget:
    """Split a model's context between history and tool results.

        working = context - prefix - reply reservation - safety

    ``reply_tokens`` is not optional slack: it is the room the model needs to
    answer. A window that fits the conversation perfectly and leaves nothing to
    reply with is the same failure as one that overflows, arriving later.

    The floors are the previous fixed constants. No model ends up with less
    continuity than it had before this module existed, even if the arithmetic
    says its context is tiny — a smaller number than today's is a regression
    dressed up as a calculation.
    """
    prefix_tokens = fixed_prefix_chars // CHARS_PER_TOKEN
    working = context_tokens - prefix_tokens - reply_tokens - safety_tokens
    if working <= 0:
        # Nothing left after the fixed costs. Fall back to the floors rather
        # than to zero: a turn with no history is bad, a turn that cannot be
        # built at all is worse.
        return ContextBudget(floor_history_chars, floor_tool_chars, max(working, 0))

    history_tokens = int(working * history_share)
    tool_tokens = working - history_tokens
    return ContextBudget(
        history_chars=max(history_tokens * CHARS_PER_TOKEN, floor_history_chars),
        tool_chars=max(tool_tokens * CHARS_PER_TOKEN, floor_tool_chars),
        working_tokens=working,
    )
