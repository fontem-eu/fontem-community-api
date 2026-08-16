"""The feed-refresh entrypoint. Its exit code is what the CronJob reports."""
# pylint: disable=missing-function-docstring,redefined-outer-name
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest

from src.domain.feed import FeedRun
from src.jobs import run_feeds


class _FakeRunner:
    def __init__(self, runs):
        self._runs = runs
        self.called = False

    async def run_all(self):
        self.called = True
        return self._runs


class _FakeContainer:
    def __init__(self, runner):
        self._runner = runner
        self.closed = False

    def __call__(self):
        @asynccontextmanager
        async def _scope():
            yield self
        return _scope()

    async def get(self, _kind):
        return self._runner

    async def close(self):
        self.closed = True


@pytest.fixture()
def wire(monkeypatch):
    def install(runs, database_url="postgresql+asyncpg://x/y"):
        runner = _FakeRunner(runs)
        container = _FakeContainer(runner)
        monkeypatch.setattr(run_feeds, "make_container", lambda _url: container)
        if database_url is None:
            monkeypatch.delenv("DATABASE_URL", raising=False)
        else:
            monkeypatch.setenv("DATABASE_URL", database_url)
        return runner, container
    return install


def _code() -> int:
    # pylint: disable-next=protected-access
    return asyncio.run(run_feeds._main())


def test_a_clean_refresh_exits_zero(wire):
    runner, container = wire([FeedRun(query_id="q", status="ok", items_new=5)])
    assert _code() == 0
    assert runner.called
    assert container.closed


def test_finding_nothing_is_a_success(wire):
    """Quiet is a normal state for a feed. Failing on an ordinary weekend
    would make the CronJob history unreadable."""
    wire([FeedRun(query_id="q", status="ok", items_seen=0, items_new=0)])
    assert _code() == 0


def test_no_published_queries_is_a_success(wire):
    wire([])
    assert _code() == 0


def test_a_failed_query_exits_nonzero(wire):
    wire([FeedRun(query_id="a", status="ok", items_new=2),
          FeedRun(query_id="b", status="error", error_message="boom")])
    assert _code() == 1


def test_truncation_is_reported_but_does_not_fail_the_run(wire):
    """Rows were dropped, which must be visible — but the items that did
    arrive are still worth having."""
    wire([FeedRun(query_id="q", status="ok", items_new=900, truncated_partitions=3)])
    assert _code() == 0


def test_a_missing_database_url_refuses_rather_than_guessing(wire):
    wire([FeedRun(query_id="q", status="ok")], database_url=None)
    assert _code() == 2


def test_the_summary_line_says_what_happened():
    run = FeedRun(query_id="q", status="ok", partitions=8, items_seen=120,
                  items_new=4, truncated_partitions=2)
    line = run.summary_line()
    assert "8 partitions" in line and "120 seen" in line and "4 new" in line
    assert "TRUNCATED" in line
