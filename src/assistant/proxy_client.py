"""HTTP client that talks to the claude-proxy SSE endpoint.

Thin wrapper around httpx. The service has a ``ProxyClient`` Protocol
that this class satisfies; nothing else in the service module knows
about HTTP.
"""
from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator

import httpx


DEFAULT_CLAUDE_PROXY_URL = os.environ.get(
    "CLAUDE_PROXY_URL",
    "http://claude-proxy.devspaces.svc.cluster.local:8090",
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
        """Yield raw SSE lines from the proxy.

        On connection errors we emit a synthetic error event so the
        caller's stream still terminates gracefully.
        """
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout)
            ) as client:
                async with client.stream(
                    "POST", self._url, json=payload
                ) as resp:
                    async for line in resp.aiter_lines():
                        if line == "":
                            yield "\n"
                        elif line.startswith("event:") or line.startswith("data:"):
                            yield line + "\n"
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            err = json.dumps({"error": str(exc)[:200]})
            yield f"event: error\ndata: {err}\n\n"
