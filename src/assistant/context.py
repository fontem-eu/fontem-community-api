"""Context and history budgeting for the assistant.

Pure functions only — no I/O, no globals. The three public entry
points are:

  - budget_context_block:  caps a caller-provided context blob.
  - truncate_history:      keeps the tail of a conversation that fits.
  - build_system_prompt:   stitches base prompt + context + history.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Turn:
    """A single chat turn as seen by the LLM."""
    role: str       # "user" or "assistant"
    content: str


@dataclass(frozen=True)
class TurnLimits:
    """Limits for truncate_history.

    ``keep_fraction`` is what makes the prompt cacheable. See
    truncate_history for why trimming to exactly the budget is the
    expensive choice.
    """
    max_turns: int = 20
    max_chars: int = 12_000
    #: When the window overflows, cut back to this fraction of the budget
    #: rather than to the budget itself.
    keep_fraction: float = 0.5


_TRUNCATION_MARKER_FMT = "\n\n[… {dropped} characters truncated …]"


def budget_context_block(block: str | None, char_budget: int) -> str:
    """Return the block unchanged if it fits, otherwise truncate with a marker.

    The marker explains to the LLM that text was dropped so it won't treat
    the result as authoritative. UTF-8 boundaries are respected via a
    chars-not-bytes approach: ``len()`` on a Python string counts codepoints.
    """
    if not block:
        return ""
    if len(block) <= char_budget:
        return block
    dropped = len(block) - char_budget
    kept = block[:char_budget]
    return kept + _TRUNCATION_MARKER_FMT.format(dropped=dropped)


def truncate_history(history: list[Turn], limits: TurnLimits) -> list[Turn]:
    """Return the tail of ``history`` that fits within ``limits``.

    The window start is quantised to multiples of a chunk, which is what
    makes the prompt cacheable:

      * Under budget nothing is dropped, so turn N+1's history starts with
        turn N's and the prompt only grows at the end.
      * Over budget, the start jumps forward a whole chunk at once and
        then stays put while the next chunk-worth of turns accumulates.

    llama.cpp reuses the longest common prefix of the prompt. A window
    that trims to exactly the budget drops one old turn per message once
    it is full, so the history block shifts on every single turn and the
    whole thing is re-prefilled — measured at ~800 tokens per turn, about
    20 seconds, on every message forever. Quantising the start means a
    trim is rare: between trims the prompt is a pure append and prefill is
    just the new message, which llama.cpp reported as f_sim 0.98 and 61
    tokens.

    Note this must not be recomputed as "smallest tail that fits". That
    is what a sliding window is, and it slides again the moment one more
    turn arrives.

    If the most recent single turn alone exceeds max_chars it is kept —
    we never return an empty history when the caller has messages.
    """
    if not history:
        return []

    n = len(history)

    def fits(drop: int, max_chars: int, max_turns: int) -> bool:
        segment = history[drop:]
        return (
            sum(len(t.content) for t in segment) <= max_chars
            and len(segment) <= max_turns
        )

    if fits(0, limits.max_chars, limits.max_turns):
        return list(history)

    # How much to discard per trim. Larger chunk, rarer trims.
    chunk = max(1, round(limits.max_turns * (1.0 - limits.keep_fraction)))

    drop = 0
    while drop < n - 1 and not fits(drop, limits.max_chars, limits.max_turns):
        drop += chunk
    drop = min(drop, n - 1)
    return list(history[drop:])

def build_system_prompt(
    base_prompt: str,
    context_block: str,
    history: list[Turn],
    site_map: str = "",
) -> str:
    """Stitch the base system prompt, caller context, and history.

    Layout, and the order matters:

        <base_prompt>
        <site_map>            <- stable across the whole conversation

        Current context:
        <context_block>       <- changes when the user navigates

        Previous conversation:
        User: ...             <- grows every turn
        Assistant: ...

    Stable first, volatile last. llama.cpp reuses the longest common
    prefix of the prompt, so anything placed after a section that changes
    is re-prefilled from scratch every turn. The site map is ~966 tokens
    of text that never changes within a conversation; it used to be
    appended after the history, which meant the whole thing was recomputed
    on every message. Measured on the local model: ~1850 tokens
    re-prefilled per turn at ~40 tok/s, about 45 seconds of pure waste.

    Empty sections are omitted entirely (no phantom headers).
    """
    parts: list[str] = [base_prompt.rstrip()]

    if site_map:
        parts.append(site_map.strip())

    if context_block:
        parts.append("Current context:\n" + context_block.rstrip())

    if history:
        rendered = []
        for turn in history:
            label = "User" if turn.role == "user" else "Assistant"
            rendered.append(f"{label}: {turn.content}")
        parts.append("Previous conversation:\n" + "\n".join(rendered))

    return "\n\n".join(parts)
