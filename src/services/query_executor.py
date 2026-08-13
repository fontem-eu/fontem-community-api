"""Executes catalogue queries by calling the read-only proxies on fontem-api.

Validation and preview have to actually run the query. They do it from the
server, not the browser: a browser-reported column list is caller-controlled,
and "is this query subscribable" is a trust decision that ends up gating what
the platform publishes.

This service holds no database drivers. It speaks HTTP to the proxies that
already exist (``/query/sql``, ``/query/cypher``, ``/sparql``), which are
read-only at the engine level, row-capped and statement-timed. Those caps are
what make it safe to let an admin press "run" on an arbitrary query: the worst
case is bounded by the proxy, not by our own good intentions.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Protocol

import httpx

DEFAULT_BASE_URL = "http://fontem-api"

# Slightly above the proxy's own 8s statement timeout, so a query that the
# proxy kills comes back as its explanatory 504 rather than as our timeout.
DEFAULT_TIMEOUT_S = 15.0

_PATHS = {
    "sql": "/query/sql",
    "cypher": "/query/cypher",
    "sparql": "/sparql",
}


@dataclass
class ExecResult:
    columns: list[str] = field(default_factory=list)
    rows: list[list] = field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    duration_ms: int = 0
    error: str | None = None


class QueryExecutor(Protocol):
    async def run(self, lang: str, query: str, params: dict | None = None) -> ExecResult:
        ...


class HttpQueryExecutor:
    """The production executor. One httpx client per request scope."""

    def __init__(self, base_url: str | None = None, timeout: float = DEFAULT_TIMEOUT_S) -> None:
        self._base = (base_url or os.environ.get("GMR_API_INTERNAL", DEFAULT_BASE_URL)).rstrip("/")
        self._timeout = timeout

    async def run(self, lang: str, query: str, params: dict | None = None) -> ExecResult:
        path = _PATHS.get(lang)
        if path is None:
            return ExecResult(error=f"unsupported engine '{lang}'")

        payload: dict = {"query": query}
        # Only send params when there are some. The SQL proxy switches psycopg
        # into interpolation mode the moment a mapping arrives, which changes
        # how a literal '%' in an unparameterised query is treated.
        if params:
            payload["params"] = params

        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(f"{self._base}{path}", json=payload)
        except httpx.HTTPError as exc:
            elapsed = int((time.monotonic() - started) * 1000)
            return ExecResult(duration_ms=elapsed, error=f"could not reach the query proxy: {exc}")
        elapsed = int((time.monotonic() - started) * 1000)

        if resp.status_code >= 400:
            return ExecResult(duration_ms=elapsed, error=_detail(resp))
        try:
            body = resp.json()
        except ValueError:
            return ExecResult(duration_ms=elapsed,
                              error="the query proxy returned a non-JSON response")

        rows = body.get("rows") or []
        return ExecResult(
            columns=list(body.get("columns") or []),
            rows=rows,
            row_count=int(body.get("row_count") or len(rows)),
            truncated=bool(body.get("truncated")),
            duration_ms=elapsed,
        )


def _detail(resp: httpx.Response) -> str:
    """Surface the proxy's own message — it explains *why* a query was
    rejected, which is the whole value of showing it to the author."""
    try:
        body = resp.json()
    except ValueError:
        return f"query proxy returned HTTP {resp.status_code}"
    if isinstance(body, dict) and body.get("detail"):
        return str(body["detail"])
    return f"query proxy returned HTTP {resp.status_code}"
