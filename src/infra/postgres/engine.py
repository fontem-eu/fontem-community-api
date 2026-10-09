"""The app's database engine: one that outlives a Postgres restart.

A restart ends every pooled connection. ``pool_pre_ping`` checks each one
at checkout and swaps a dead one for a fresh one, so the request that
follows never sees it — but only when the ping fails with a database error.
When the server's termination reaches asyncpg while it is pinging, asyncpg
raises its own ``InternalClientError`` ("cannot switch to state 15; another
operation is in progress"), which SQLAlchemy does not recognise as a lost
connection and passes on to the request. It did, in about one restart in
three (tests/integration/test_db_reconnect_integration.py).
"""
from __future__ import annotations

import asyncpg
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


def make_engine(database_url: str) -> AsyncEngine:
    """An engine whose pooled connections are checked before each use, and
    for which any failed check means "reconnect", never an error."""
    engine = create_async_engine(database_url, echo=False, pool_pre_ping=True)
    dialect = engine.sync_engine.dialect
    ping = dialect.do_ping

    def do_ping(dbapi_connection) -> bool:
        try:
            return ping(dbapi_connection)
        except asyncpg.InternalClientError:
            # The connection was ended mid-ping: it is gone, like any other
            # connection whose ping fails, and the pool replaces it.
            return False

    dialect.do_ping = do_ping
    return engine
