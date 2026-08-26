"""Alembic migrations run cleanly on an empty database.

The unit and integration suites build their schema from ``Base.metadata``,
which means the migrations — the thing that actually runs in production, as an
ArgoCD PreSync hook — are otherwise never executed by CI. This repo has been
burnt by exactly that gap before: the schema drifted from the models until
/auth/login started returning 500. So: upgrade a fresh database to head, then
downgrade the newest revision and upgrade again, which is the only way to find
out whether the downgrade is real or decorative.

Alembic runs in a SUBPROCESS, not in-process. Two reasons, one of them found
the hard way: ``env.py`` calls ``logging.config.fileConfig``, which disables
every existing logger, so an in-process upgrade silently blinds ``caplog`` for
the rest of the session and unrelated tests start failing. A subprocess is
also what production actually does.
"""
# pylint: disable=missing-function-docstring,redefined-outer-name
from __future__ import annotations

import os
import subprocess
import sys

import pytest
import sqlalchemy as sa
from testcontainers.postgres import PostgresContainer

CATALOGUE_TABLES = ("named_queries", "query_groups", "query_group_members")
BRIEFING_TABLES = ("feed_items", "feed_runs", "watches")


def _alembic(url: str, *args: str) -> None:
    # env.py deliberately reads DATABASE_URL rather than the ini, so that
    # `alembic upgrade` can never be aimed at a different database than the
    # service whose models it migrates — including its async driver.
    env = {**os.environ,
           "DATABASE_URL": url.replace("postgresql://", "postgresql+asyncpg://")}
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        env=env, capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, f"alembic {' '.join(args)} failed:\n{proc.stderr}"


@pytest.fixture(scope="module")
def migrated_url():
    with PostgresContainer("postgres:16-alpine") as pg:
        url = pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")
        engine = sa.create_engine(url)
        with engine.begin() as conn:
            # gen_random_uuid() is built in on 16, but the extension keeps the
            # column defaults working on an older server too.
            conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        engine.dispose()
        _alembic(url, "upgrade", "head")
        yield url


@pytest.fixture()
def inspector(migrated_url):
    engine = sa.create_engine(migrated_url)
    try:
        yield sa.inspect(engine)
    finally:
        engine.dispose()


def test_upgrade_to_head_creates_the_catalogue_tables(inspector):
    names = set(inspector.get_table_names())
    for table in CATALOGUE_TABLES:
        assert table in names, f"{table} missing after upgrade"


def test_upgrade_to_head_creates_the_briefing_tables(inspector):
    names = set(inspector.get_table_names())
    for table in BRIEFING_TABLES:
        assert table in names, f"{table} missing after upgrade"


def test_an_item_is_unique_per_query_which_is_what_makes_dedup_work(inspector):
    """The runner re-reads an overlapping window every run and relies on this
    constraint to discard what it has already seen."""
    uniques = {tuple(sorted(u["column_names"]))
               for u in inspector.get_unique_constraints("feed_items")}
    assert ("item_id", "query_id") in uniques


def test_membership_is_a_composite_key_so_a_query_can_join_many_groups(inspector):
    pk = inspector.get_pk_constraint("query_group_members")
    assert set(pk["constrained_columns"]) == {"group_id", "query_id"}


def test_status_and_lang_are_constrained_at_the_database(migrated_url):
    engine = sa.create_engine(migrated_url)
    try:
        with engine.begin() as conn:
            conn.execute(sa.text(
                "INSERT INTO named_queries (slug, lang, status) VALUES ('ok','sql','draft')"
            ))
        for bad in (
            "INSERT INTO named_queries (slug, lang, status) VALUES ('a','sql','whenever')",
            "INSERT INTO named_queries (slug, lang, status) VALUES ('b','mongo','draft')",
        ):
            with pytest.raises(sa.exc.IntegrityError), engine.begin() as conn:
                conn.execute(sa.text(bad))
    finally:
        engine.dispose()


def test_the_catalogue_downgrade_is_real(migrated_url):
    """Named by revision, not by "-1". This originally downgraded one step
    because 012 was head at the time; the moment anything landed on top, the
    test was downgrading somebody else's migration and asserting about tables
    it had not touched."""
    _alembic(migrated_url, "downgrade", "011")
    engine = sa.create_engine(migrated_url)
    try:
        names = set(sa.inspect(engine).get_table_names())
        assert not set(CATALOGUE_TABLES) & names
    finally:
        engine.dispose()
    _alembic(migrated_url, "upgrade", "head")


_PRE_019_TABLE = """
    CREATE TABLE assist_conversations (
        id UUID PRIMARY KEY,
        user_id UUID NOT NULL,
        conversation_key TEXT NOT NULL,
        title TEXT,
        {extra}
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
"""


def _db_at_018_with_conversations(pg, extra: str = ""):
    """A database in the shape a deployed one is actually in when 019 runs.

    `assist_conversations` is created by 002 and then *dropped* by 008 — an
    autogenerate accident that is now load-bearing history — so a fresh
    `upgrade head` leaves no such table and `create_all` rebuilds it from the
    models. That is why 018 and 019 both guard on table existence, and why
    asserting the columns after a plain `upgrade head` asserts a premise that
    was never true.

    So: migrate to 018, let create_all's equivalent put the table back, and
    only then run 019.
    """
    url = pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")
    engine = sa.create_engine(url)
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
    _alembic(url, "upgrade", "018")
    with engine.begin() as conn:
        conn.execute(sa.text(_PRE_019_TABLE.format(extra=extra)))
    return url, engine


def test_019_adds_the_summary_columns_where_the_table_already_exists():
    """The production path: the table is there, the columns are not.

    The migration runs as an ArgoCD PreSync hook, so a column the model reads
    but the database lacks fails the whole rollout rather than one request.
    """
    with PostgresContainer("postgres:16-alpine") as pg:
        url, engine = _db_at_018_with_conversations(pg)
        _alembic(url, "upgrade", "head")
        cols = {c["name"]: c for c in sa.inspect(engine).get_columns("assist_conversations")}
        engine.dispose()

    assert {"summary", "summary_through"} <= set(cols)
    # Nullable: a conversation that never overflowed has no summary, and on a
    # large-context model that is every conversation.
    assert cols["summary"]["nullable"] is True
    assert cols["summary_through"]["nullable"] is True


def test_019_is_idempotent_when_the_columns_are_already_there():
    """Sequential revision ids collide across branches here, so a migration
    already applied under another id must not fail the deploy."""
    with PostgresContainer("postgres:16-alpine") as pg:
        url, engine = _db_at_018_with_conversations(
            pg, extra="summary TEXT, summary_through TEXT,",
        )
        engine.dispose()
        # Must not raise: _alembic asserts a zero exit code.
        _alembic(url, "upgrade", "head")
