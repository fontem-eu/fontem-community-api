"""What a tool call actually did, sent to the panel so it can be seen.

The stream has always announced *that* a tool ran — a `status` event with the
tool's name, emitted before it executes. What came back was invisible: the
model read it, acted on it, and the only evidence in the UI was whatever the
model chose to say afterwards. When the assistant reports something odd, that
leaves no way to tell a bad tool result from a bad reading of a good one.

This adds the other half. One `tool_result` event per call, carrying what the
tool returned, so the panel can render the model's working as chat history.

Deliberately reports the result the MODEL saw, after `tool_budget` capping,
not the raw response. If the model answered from a truncated view, that is
the thing worth seeing — showing the untruncated original would explain the
answer less well, not more. `bytes` and `truncated` say how much was cut.

Capped again for display: a debugging view that ships a megabyte per tool
call into the browser stops being usable at exactly the moment it matters,
on the fat results worth inspecting.
"""
from __future__ import annotations

import json

EVENT = "tool_result"

#: Generous — the point is to see the payload — but bounded. Well above the
#: 8k a single result can carry into the model, so the common case is whole.
MAX_DISPLAY_CHARS = 20_000

DISPLAY_TRUNCATED = "\n\n[... {dropped} more characters not shown here ...]"


def trace(name: str, args: dict, result: str, elapsed: float,
          raw_len: int | None = None) -> dict:
    """The payload for one `tool_result` event."""
    text = result if isinstance(result, str) else json.dumps(result, default=str)
    shown = text
    if len(text) > MAX_DISPLAY_CHARS:
        shown = text[:MAX_DISPLAY_CHARS] + DISPLAY_TRUNCATED.format(
            dropped=len(text) - MAX_DISPLAY_CHARS)
    original = raw_len if raw_len is not None else len(text)
    return {
        "tool": name,
        "args": args if isinstance(args, dict) else {},
        "result": shown,
        "bytes": original,
        # True when the MODEL saw less than the tool produced — the case that
        # explains an answer that looks like it ignored data.
        "truncated": original > len(text),
        "elapsed": round(elapsed, 1),
    }
