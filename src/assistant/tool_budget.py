"""Keep one turn's tool output inside the context the model actually has.

A tool result is unbounded. An entity search on a well-connected company
produced ~90k tokens during the model evaluation. Every result is appended to
the conversation and the conversation is re-sent whole on the next round, so
a single fat result puts the request over the server's per-slot context and
llama.cpp rejects it outright:

    request (36458 tokens) exceeds the available context size (16384 tokens)

That is not a degraded answer, it is a dead turn: the stream emits
``event: error``, no proposal card renders, and the assistant appears to have
stopped working. `TurnLimits` already bounds stored history — this is the
other half, the results produced inside the turn, which nothing bounded.

The eval harness capped these from its first run; production did not. The
measurement rig was safe while the product was not, which is why the gap
stayed invisible until a browser drove the real path.
"""
from __future__ import annotations

#: Ceiling on a single tool result before it enters the conversation.
MAX_TOOL_RESULT_CHARS = 8_000

#: Ceiling on ALL tool results in one turn, together.
#:
#: The per-result cap alone does not bound the turn: `_MAX_TOOL_ITERATIONS`
#: is 10, and ten 8k results overflow a 16k-token window on their own. Sized
#: Sized against the smallest context we serve: 16384 tokens.
#:
#: 24_000 was the first estimate, derived from a 4 chars/token rule of
#: thumb. It cut the observed overflow from 36458 tokens to 16624 — still
#: over, by 240. The rule of thumb was the error: JSON tool output is
#: punctuation-dense and runs closer to 3 chars/token, so the same
#: characters buy fewer tokens than assumed. 14_000 leaves real headroom
#: for the system prompt, the catalogue block, the tool schemas and up to
#: `TurnLimits.max_chars` of history, measured rather than estimated.
MAX_TOOL_RESULT_CHARS_PER_TURN = 14_000

TRUNCATED_MARKER = (
    '\n\n[... truncated: {dropped} of {total} characters omitted. '
    'Narrow the query — by country, year or name — to see the rest.]'
)

#: What the model gets once the turn's whole budget is spent. Phrased as an
#: instruction rather than an error: an empty result or an error string reads
#: to the model as a tool that returned nothing, and the observed response to
#: that is to call another tool — the opposite of what a spent budget needs.
BUDGET_EXHAUSTED = (
    "[omitted: this turn's tool-output budget is spent. Do not call more "
    "tools; answer from the results you already have, and say which parts "
    "of the question they do not cover.]"
)


def cap_tool_result(result: str, remaining: int) -> tuple[str, int]:
    """Trim one tool result to fit the turn's remaining budget.

    Returns the text to send and the budget left after it.

    Truncation is announced in-band. Cut silently, the model reports the
    first 8k characters as the whole record, and on a transparency platform
    a confident partial count is worse than an explicit gap — nothing
    downstream can tell the two apart. Fixing an overflow must not introduce
    a fabrication.
    """
    if remaining <= 0:
        return BUDGET_EXHAUSTED, 0
    allowance = min(MAX_TOOL_RESULT_CHARS, remaining)
    if len(result) <= allowance:
        return result, remaining - len(result)
    kept = result[:allowance]
    return (
        kept + TRUNCATED_MARKER.format(
            dropped=len(result) - allowance, total=len(result),
        ),
        remaining - allowance,
    )
