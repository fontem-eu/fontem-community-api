"""Refresh the Briefings feed: run every published query, materialise results.

Run as a Kubernetes CronJob rather than an in-process timer. A timer inside
the API would fire once per replica — two replicas means two concurrent runs
over the same window, which the unique constraint would survive but which
doubles the load on the query proxy for nothing. A CronJob is also visible:
its history says whether last night's refresh happened, which an asyncio task
buried in a web process does not.

Exit codes are for the CronJob's own reporting:
  0  every query ran, whatever it found
  1  at least one query failed outright
A run that found nothing is a success. Quiet is a normal state for a feed,
and failing on it would make an ordinary weekend look like an outage.
"""
from __future__ import annotations

import asyncio
import os
import sys

from loguru import logger

from src.api.di import make_container
from src.services.feed_runner import FeedRunner

#: One line of a feed run's output, at whichever level it was emitted.
_RUN_LINE = "feed run {}: {}"


async def _main() -> int:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        logger.error("DATABASE_URL is not set — refusing to guess which database to read")
        return 2

    container = make_container(database_url)
    try:
        async with container() as request_container:
            runner = await request_container.get(FeedRunner)
            runs = await runner.run_all()
    finally:
        await container.close()

    if not runs:
        logger.info("no published queries to refresh")
        return 0

    failed = 0
    for run in runs:
        line = run.summary_line()
        if run.status == "error":
            failed += 1
            logger.error(_RUN_LINE, run.query_id, line)
        elif run.truncated_partitions:
            # Not a failure, but it means rows were dropped, and that is
            # exactly the thing this design refuses to let pass silently.
            logger.warning(_RUN_LINE, run.query_id, line)
        else:
            logger.info(_RUN_LINE, run.query_id, line)

    total_new = sum(r.items_new for r in runs)
    logger.info("refreshed {} queries, {} new items, {} failed",
                len(runs), total_new, failed)
    return 1 if failed else 0


def main() -> None:
    sys.exit(asyncio.run(_main()))


if __name__ == "__main__":
    main()
