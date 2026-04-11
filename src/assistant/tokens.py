"""Token estimation and SSE usage parsing.

We care about two things:
  * Estimating input tokens **before** a request, as a fallback when
    the proxy has not yet forwarded the real count (e.g. if the stream
    is cancelled mid-flight).
  * Parsing the ``event: usage`` block the claude-proxy emits at the
    end of each stream. The proxy extracts ``input_tokens`` and
    ``output_tokens`` from Claude CLI's ``result`` event and forwards
    them as a structured SSE event in the shape::

        event: usage
        data: {"input_tokens": 123, "output_tokens": 45}

    This is the source of truth for per-user billing. Estimates only
    kick in when the proxy didn't get a chance to forward the event.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class TokenUsage:
    """Input + output token counts as reported by the LLM."""
    input_tokens: int
    output_tokens: int


# Roughly: English prose is ~1.3 tokens per word for BPE tokenizers.
# We count word-ish chunks (alphanumeric runs + single symbols) and
# apply that ratio. This is deliberately simple — no tiktoken dep.
_TOKEN_FUDGE = 1.3
_WORD_RE = re.compile(r"\w+|[^\w\s]")


def estimate_tokens(text: str | None) -> int:
    """Return a rough token count for ``text``.

    Zero for empty or None. Scales linearly with length. Never raises.
    """
    if not text:
        return 0
    words = _WORD_RE.findall(text)
    return max(1, int(len(words) * _TOKEN_FUDGE))


def parse_sse_usage(event: str, data: str) -> TokenUsage | None:
    """Pull a ``TokenUsage`` out of a ``event: usage`` SSE block.

    The proxy emits this once per request, at the end of the stream,
    with the real token counts extracted from Claude CLI's ``result``
    event. Returns ``None`` for any other event, any non-JSON payload,
    or missing/invalid fields. Never raises.
    """
    if event != "usage":
        return None
    try:
        payload = json.loads(data)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    inp = payload.get("input_tokens")
    out = payload.get("output_tokens")
    if not isinstance(inp, int) or not isinstance(out, int):
        return None
    if inp < 0 or out < 0:
        return None
    return TokenUsage(input_tokens=inp, output_tokens=out)


def total_tokens(usage: TokenUsage | None) -> int:
    """Input + output, or zero if usage is None."""
    if usage is None:
        return 0
    return usage.input_tokens + usage.output_tokens
