"""Token estimation and SSE usage parsing.

We care about two things:
  * Estimating input tokens **before** a request, so we can charge a
    user's budget and log the cost of every prompt. The estimate does
    not need to match the LLM's tokenizer byte-for-byte — word-count-
    with-a-fudge-factor is good enough for billing at this stage.
  * Parsing the `usage` object from an Anthropic-style SSE event, if
    the proxy ever forwards it. When it doesn't, callers fall back to
    estimate_tokens on the streamed text.
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
    """Pull a ``TokenUsage`` out of an Anthropic-style SSE event.

    Returns None if the event isn't a usage-bearing one, the payload
    isn't JSON, or the required fields are missing. Never raises.
    """
    if event not in {"message_stop", "message_delta"}:
        return None
    try:
        payload = json.loads(data)
    except (ValueError, TypeError):
        return None
    usage = payload.get("usage") if isinstance(payload, dict) else None
    if not isinstance(usage, dict):
        return None
    inp = usage.get("input_tokens")
    out = usage.get("output_tokens")
    if not isinstance(inp, int) or not isinstance(out, int):
        return None
    return TokenUsage(input_tokens=inp, output_tokens=out)


def total_tokens(usage: TokenUsage | None) -> int:
    """Input + output, or zero if usage is None."""
    if usage is None:
        return 0
    return usage.input_tokens + usage.output_tokens
