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
    """Sliding-window limits for truncate_history."""
    max_turns: int = 20
    max_chars: int = 12_000


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

    Rules:
      * Drops oldest turns first (sliding window).
      * Respects both max_turns and max_chars; whichever kicks in earlier wins.
      * If the most recent single turn alone exceeds max_chars, it is kept —
        we never return an empty history when the caller has messages.
    """
    if not history:
        return []

    # Start from the tail and include turns until a budget is exhausted.
    out: list[Turn] = []
    chars = 0
    for turn in reversed(history):
        if len(out) >= limits.max_turns:
            break
        next_chars = chars + len(turn.content)
        if next_chars > limits.max_chars and out:
            # Stop before we exceed the char budget, unless we haven't
            # kept anything yet (then we must keep this single turn).
            break
        out.append(turn)
        chars = next_chars

    out.reverse()
    return out


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
