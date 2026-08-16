"""A scriptable stand-in for the query proxies.

Validation runs a query twice with identical binds to prove item_id is
intrinsic rather than positional, so a fake that returns the same thing
forever cannot exercise the interesting case. This one is a queue: push the
results you want, in order, and the second run can differ from the first.
"""
from __future__ import annotations

from src.services.query_executor import ExecResult

CONTRACT_COLUMNS = ["item_id", "item_time", "nuts", "rank_value", "title", "link"]


def ok_result(rows=None, columns=None, duration_ms: int = 12) -> ExecResult:
    rows = rows if rows is not None else [
        ["contract:1", "2026-08-01T00:00:00+00:00", "PT17", 1_194_208, "A contract",
         "https://x/1"],
    ]
    return ExecResult(
        columns=list(columns or CONTRACT_COLUMNS),
        rows=rows, row_count=len(rows), duration_ms=duration_ms,
    )


class FakeQueryExecutor:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self._queue: list[ExecResult] = []
        self.default = ok_result()

    def push(self, *results: ExecResult) -> "FakeQueryExecutor":
        self._queue.extend(results)
        return self

    def reset(self) -> None:
        self.calls.clear()
        self._queue.clear()
        self.default = ok_result()

    async def run(self, lang: str, query: str, params: dict | None = None) -> ExecResult:
        self.calls.append({"lang": lang, "query": query, "params": params})
        if self._queue:
            return self._queue.pop(0)
        return self.default
