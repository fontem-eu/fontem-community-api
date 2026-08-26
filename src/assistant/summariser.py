"""A rolling summary of what fell out of the continuity window.

Only reached on overflow. A conversation that fits its budget produces no
summary and pays for none — which on a 1M-token model is effectively never,
and on the smallest local model is routine. Same code path, different trigger
point, and the expensive one only runs when it buys something.

The summary is *rolling*: each time turns fall off, the previous summary and
the newly dropped turns are folded into one. That keeps the cost proportional
to what was just lost rather than to the whole conversation, and it means the
summary never grows without bound.
"""
from __future__ import annotations

from src.assistant.context import Turn

#: The summary is prepended to the window, so it spends the same budget the
#: turns it replaces were spending. Kept small deliberately: its job is to
#: stop the model contradicting an earlier decision, not to be a transcript.
MAX_SUMMARY_CHARS = 2_000

SYSTEM_PROMPT = (
    "You compress the earlier part of a conversation that no longer fits the "
    "model's context.\n\n"
    "Write a terse third-person note recording only what still constrains the "
    "conversation: decisions taken, facts established, identifiers and names "
    "in play, and anything the user asked for that is not done yet.\n\n"
    "Omit pleasantries, restated questions, and anything already superseded. "
    "Do not speculate, do not add advice, and do not invent detail that is "
    "not in the text. If an earlier summary is provided, fold the new "
    "material into it and return one note, not two.\n\n"
    f"Hard limit: {MAX_SUMMARY_CHARS} characters."
)

#: How a stored summary re-enters the window. Marked as a note rather than
#: dialogue so the model does not read it as something a person said.
SUMMARY_PREFIX = "Earlier in this conversation: "


def unsummarised(history: list[Turn], through_message_id: str) -> list[Turn]:
    """The turns the stored summary does not already cover.

    Without this the window is re-derived from the whole conversation every
    turn, so the same turns fall off again and get folded into the summary
    again — the summary would restate its own contents, growing and blurring
    with every message. The marker is what makes the summary roll rather than
    accumulate.

    An unknown marker means the row it named is gone (a deletion, or a summary
    written before the marker existed). Treating that as "covers nothing" is
    the safe reading: it re-summarises material that is already represented,
    which is wasteful, where the other reading would silently drop turns the
    summary never saw.
    """
    if not through_message_id:
        return list(history)
    for i, turn in enumerate(history):
        if turn.message_id and turn.message_id == through_message_id:
            return list(history[i + 1:])
    return list(history)


def render(summary: str) -> Turn:
    """The window row for a stored summary."""
    return Turn(role="assistant", content=SUMMARY_PREFIX + summary.strip())


def build_request(previous: str, dropped: list[Turn]) -> str:
    """The user message for one summarisation call.

    Returns "" when there is nothing to fold in, which the caller treats as
    "keep the summary you have" rather than as an empty summary.
    """
    if not dropped:
        return ""
    lines: list[str] = []
    if previous:
        lines.append("Earlier summary:\n" + previous.strip() + "\n")
    lines.append("New material that has fallen out of the window:")
    for t in dropped:
        if t.role == "tool":
            head = f"Tool {t.name}" if t.name else "Tool"
            lines.append(f"{head}: {t.content or '(called)'}")
        else:
            lines.append(f"{'User' if t.role == 'user' else 'Assistant'}: {t.content}")
    return "\n".join(lines)


def cap(summary: str) -> str:
    """Bound a model-produced summary; it is not trusted to obey the limit."""
    text = (summary or "").strip()
    return text if len(text) <= MAX_SUMMARY_CHARS else text[:MAX_SUMMARY_CHARS]
