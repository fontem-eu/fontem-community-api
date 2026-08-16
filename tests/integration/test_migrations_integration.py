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
