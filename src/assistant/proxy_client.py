"""HTTP client that talks to the claude-proxy SSE endpoint.

Thin wrapper around httpx. The service has a ``ProxyClient`` Protocol
that this class satisfies; nothing else in the service module knows
about HTTP.

Contract: ``stream(payload)`` yields **whole SSE event blocks**. Each
yielded string is a complete ``event: …\\ndata: …\\n\\n`` block. This
matches the SSE spec and lets downstream parsers treat each yielded
value as one event, rather than reassembling partial lines — which is
where the old line-by-line version silently dropped half the payload.
"""
from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator

import httpx


DEFAULT_CLAUDE_PROXY_URL = os.environ.get(
    "CLAUDE_PROXY_URL",
    "http://claude-proxy.gmr.svc.cluster.local:8090",
)


class ClaudeProxyClient:

    def __init__(
        self,
        url: str = DEFAULT_CLAUDE_PROXY_URL,
        timeout: float = 300.0,
    ) -> None:
        self._url = url.rstrip("/") + "/chat/stream"
        self._timeout = timeout

    async def stream(self, payload: dict) -> AsyncIterator[str]:
        """Yield one complete SSE event block per iteration.

        httpx's ``aiter_lines`` gives us one line at a time. We buffer
        lines until we see a blank line (the SSE event terminator) and
        flush the whole block as a single string. On connection errors
        we emit a synthetic ``event: error`` block so the caller's
        stream still terminates gracefully.
        """
        block_lines: list[str] = []
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout)
            ) as client:
                async with client.stream(
                    "POST", self._url, json=payload
                ) as resp:
                    async for line in resp.aiter_lines():
                        if line == "":
                            if block_lines:
                                yield "\n".join(block_lines) + "\n\n"
                                block_lines = []
                            continue
                        if line.startswith(":"):
                            # SSE comment / keep-alive — ignore
                            continue
                        if line.startswith("event:") or line.startswith("data:"):
                            block_lines.append(line)
            # Flush any trailing block on clean stream close
            if block_lines:
                yield "\n".join(block_lines) + "\n\n"
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            err = json.dumps({"error": str(exc)[:200]})
            yield f"event: error\ndata: {err}\n\n"
