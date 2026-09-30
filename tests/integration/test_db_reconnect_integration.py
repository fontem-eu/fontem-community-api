"""A database restart must not cost the next request.

When Postgres restarts, every connection in the app's pool is dead. Without
a liveness check at checkout, the first request on each of them fails, and
the session it ran in ends in PendingRollbackError: that is how the
promotion gate's seed step got a 500 from community-api an hour after the
staging database was restarted (2026-09-29). The engine the app builds must
hand out a working connection instead.
"""
# pylint: disable=missing-function-docstring,redefined-outer-name
from __future__ import annotations

import asyncio

import asyncpg
import pytest
import sqlalchemy as sa
from dishka import make_async_container
from sqlalchemy.ext.asyncio import AsyncEngine
from testcontainers.postgres import PostgresContainer

from src.api.di import DatabaseProvider


@pytest.fixture(scope="module")
def pg_url():
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")


def test_the_first_query_after_the_server_drops_every_connection_succeeds(pg_url):
    async def run():
        container = make_async_container(DatabaseProvider(pg_url.replace("postgresql://", "postgresql+asyncpg://")))
        try:
            engine = await container.get(AsyncEngine)
            async with engine.connect() as conn:  # leaves one connection in the pool
                assert (await conn.execute(sa.text("SELECT 1"))).scalar() == 1

            # What a restart does to that pool: the server ends every session.
            admin = await asyncpg.connect(pg_url)
            try:
                ended = await admin.fetchval(
                    "SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity "
                    "WHERE pid <> pg_backend_pid() AND datname = current_database()")
            finally:
                await admin.close()
            assert ended >= 1

            async with engine.connect() as conn:
                assert (await conn.execute(sa.text("SELECT 2"))).scalar() == 2
        finally:
            await container.close()

    asyncio.run(run())
