"""The runner: paging, truncation, and the ingestion timestamp."""
# pylint: disable=missing-function-docstring,redefined-outer-name
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from src.domain.named_query import NamedQuery
from src.infra.memory.mem_feed_repo import InMemoryFeedRepository
from src.infra.memory.mem_named_query_repo import InMemoryNamedQueryRepository
from src.services.feed_runner import EU_COUNTRIES, PROXY_ROW_CAP, FeedRunner
from src.services.query_executor import ExecResult
from tests.fake_query_executor import CONTRACT_COLUMNS, FakeQueryExecutor

NOW = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _rows(n, prefix="c", nuts="PT17", when="2026-08-14T00:00:00+00:00"):
    return [[f"{prefix}{i}", when, nuts, 1000 + i, f"Title {i}", f"https://x/{i}", ""]
            for i in range(n)]


def _result(n, truncated=False, **kw):
    rows = _rows(n, **kw)
    return ExecResult(columns=CONTRACT_COLUMNS + ["summary"], rows=rows,
                      row_count=len(rows), truncated=truncated)


@pytest.fixture()
def setup():
    catalogue = InMemoryNamedQueryRepository()
    feed = InMemoryFeedRepository()
    executor = FakeQueryExecutor()
    query = _run(catalogue.create_query(NamedQuery(
        slug="public-contracts", lang="cypher", status="published",
        query="MATCH (c) WHERE c.g IN $nuts AND c.t > $since RETURN c")))
    runner = FeedRunner(queries=catalogue, feed=feed, executor=executor, lag_days=1)
    return catalogue, feed, executor, query, runner


def test_a_day_that_fits_costs_one_request(setup):
    _, _, executor, query, runner = setup
    executor.default = _result(5)
    run = _run(runner.run_query(query, now=NOW))
    # lag_days=1 → two days in the window, one request each.
    assert run.partitions == 2
    assert len(executor.calls) == 2
    assert run.truncated_partitions == 0


def test_a_capped_day_is_split_by_country(setup):
    """Measured on prod: one EU-wide day of procurement came back at 741 rows
    once and at the 1000-row cap the next. Paging by day alone is not enough."""
    _, _, executor, query, runner = setup
    executor.default = _result(PROXY_ROW_CAP, truncated=True)
    run = _run(runner.run_query(query, now=NOW))

    # Two days, each split into 27 country partitions. The whole-day probe is
    # not counted: its result was discarded and replaced, so counting it would
    # inflate the run. It still costs a REQUEST, which is why the call count
    # is 28 a day and the partition count is 27.
    assert run.partitions == 2 * len(EU_COUNTRIES)
    assert len(executor.calls) == 2 * (1 + len(EU_COUNTRIES))
    asked = [c["params"]["nuts"] for c in executor.calls]
    assert ["EU"] in asked
    assert ["PT"] in asked and ["DE"] in asked


def test_a_quiet_query_is_never_split(setup):
    """Splitting only on truncation is what keeps a quiet query at one
    request a day instead of twenty-seven."""
    _, _, executor, query, runner = setup
    executor.default = _result(3)
    _run(runner.run_query(query, now=NOW))
    assert all(c["params"]["nuts"] == ["EU"] for c in executor.calls)


def test_truncation_that_survives_splitting_is_counted_not_shrugged_off(setup):
    _, _, executor, query, runner = setup
    executor.default = _result(PROXY_ROW_CAP, truncated=True)
    run = _run(runner.run_query(query, now=NOW))
    # Every split partition is still at the cap, so every one is recorded.
    assert run.truncated_partitions == run.partitions
    assert run.status == "ok"          # rows were still written


def test_items_are_written_once_and_first_seen_at_never_moves(setup):
    """first_seen_at is the only ingestion timestamp in the system. A value
    that moves on every re-scan is not a timestamp."""
    _, feed, executor, query, runner = setup
    executor.default = _result(4)

    first = _run(runner.run_query(query, now=NOW))
    assert first.items_new == 4
    stamps = {(i.item_id, i.first_seen_at) for i in feed.all_items()}

    second = _run(runner.run_query(query, now=NOW))
    assert second.items_seen > 0        # it re-read the window
    assert second.items_new == 0        # and wrote nothing new
    assert {(i.item_id, i.first_seen_at) for i in feed.all_items()} == stamps


def test_a_late_arrival_is_caught_by_the_overlapping_window(setup):
    """The point of re-reading: an item published two days ago that only
    reached us today is still new to us, and the item_id is what says so."""
    _, _, executor, query, runner = setup
    executor.default = _result(2)
    _run(runner.run_query(query, now=NOW))

    executor.default = _result(3)      # a third item appears for the same day
    run = _run(runner.run_query(query, now=NOW))
    assert run.items_new == 1


def test_a_failing_partition_does_not_lose_the_rest_of_the_window(setup):
    _, _, executor, query, runner = setup
    executor.push(ExecResult(error="Cypher error: transient"))
    executor.default = _result(2)
    run = _run(runner.run_query(query, now=NOW))
    assert run.items_new == 2          # the second day still landed
    assert run.status == "ok"
    assert "transient" in (run.error_message or "")


def test_a_run_where_nothing_worked_is_an_error(setup):
    _, _, executor, query, runner = setup
    executor.default = ExecResult(error="Cypher error: label does not exist")
    run = _run(runner.run_query(query, now=NOW))
    assert run.status == "error"
    assert "label does not exist" in run.error_message


def test_rows_missing_a_contract_column_are_skipped_not_half_written(setup):
    _, feed, executor, query, runner = setup
    executor.default = ExecResult(
        columns=["item_id", "title"], rows=[["a", "b"]], row_count=1)
    run = _run(runner.run_query(query, now=NOW))
    assert run.items_new == 0
    assert feed.all_items() == []


def test_an_unparseable_item_time_is_dropped(setup):
    _, _, executor, query, runner = setup
    executor.default = _result(2, when="not-a-date")
    run = _run(runner.run_query(query, now=NOW))
    assert run.items_new == 0


def test_a_bare_region_code_is_accepted_as_well_as_a_list(setup):
    """Forcing every query to wrap one region in a list would be ceremony."""
    _, feed, executor, query, runner = setup
    rows = [["x", "2026-08-14T00:00:00+00:00", ["PT17", "PT16"], 5, "T", "u", ""],
            ["y", "2026-08-14T00:00:00+00:00", "ES300", 6, "T", "u", ""]]
    executor.default = ExecResult(columns=CONTRACT_COLUMNS + ["summary"],
                                  rows=rows, row_count=2)
    _run(runner.run_query(query, now=NOW))
    stored = {i.item_id: i.nuts for i in feed.all_items()}
    assert stored["x"] == ["PT17", "PT16"]
    assert stored["y"] == ["ES300"]


def test_run_all_keeps_going_when_one_query_explodes(setup):
    catalogue, _, executor, _, runner = setup
    _run(catalogue.create_query(NamedQuery(
        slug="second", lang="cypher", status="published",
        query="MATCH (c) WHERE c.g IN $nuts AND c.t > $since RETURN c")))
    _run(catalogue.create_query(NamedQuery(slug="draft-one", lang="cypher", status="draft")))
    executor.default = _result(1)
    runs = _run(runner.run_all())
    assert len(runs) == 2          # both published; the draft is not run
