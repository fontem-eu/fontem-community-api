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


def _db_at_019_with_an_edited_article(pg):
    """A database as production had it: one article, one section, and the
    save history that section accumulated."""
    url = pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")
    engine = sa.create_engine(url)
    _alembic(url, "upgrade", "019")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO users (id, email, name, failed_login_attempts) "
            "VALUES ('11111111-1111-1111-1111-111111111111', "
            "        'a@example.com', 'A', 0)"
        ))
        conn.execute(sa.text(
            "INSERT INTO reports "
            "  (id, title, created_by, visibility, language, nuts_region, "
            "   content_version) "
            "VALUES ('22222222-2222-2222-2222-222222222222', 'Story', "
            "        '11111111-1111-1111-1111-111111111111', 'private', "
            "        'en', '', 1)"
        ))
        conn.execute(sa.text(
            "INSERT INTO sections (id, report_id, sort_order, content_json) "
            "VALUES ('33333333-3333-3333-3333-333333333333', "
            "        '22222222-2222-2222-2222-222222222222', 0, "
            "        '{\"tiptap\": {\"t\": \"current\"}, \"version\": 2}')"
        ))
        for n, when in ((1, "2026-08-01"), (2, "2026-08-02")):
            conn.execute(sa.text(
                "INSERT INTO section_versions "
                "  (section_id, content_json, saved_by, saved_at) "
                "VALUES ('33333333-3333-3333-3333-333333333333', "
                f"       '{{\"tiptap\": {{\"t\": \"v{n}\"}}, \"version\": 2}}', "
                "        '11111111-1111-1111-1111-111111111111', "
                f"       '{when}')"
            ))
    return url, engine


def test_020_carries_the_existing_history_into_the_revision_chain():
    """The article's past is data, not scaffolding.

    Every section_versions row becomes a revision, oldest first and
    parent-linked, with the section's current content as the newest and
    main pointing at it. Dropping that history while building a feature
    whose point is keeping history would be perverse — and it is the
    history that made the lost-widgets incident reconstructable at all.
    """
    with PostgresContainer("postgres:16-alpine") as pg:
        url, engine = _db_at_019_with_an_edited_article(pg)
        _alembic(url, "upgrade", "head")
        with engine.begin() as conn:
            revisions = conn.execute(sa.text(
                "SELECT id, parent_id, content_json->'tiptap'->>'t' "
                "FROM doc_revisions ORDER BY created_at"
            )).fetchall()
            branch = conn.execute(sa.text(
                "SELECT owner_id, head_revision_id FROM doc_branches"
            )).fetchall()
        engine.dispose()

    ids = [r[0] for r in revisions]
    parents = [r[1] for r in revisions]
    texts = [r[2] for r in revisions]

    assert texts == ["v1", "v2", "current"]
    # Parent-linked in save order: the chain, not a pile.
    assert parents[0] is None
    assert parents[1] == ids[0]
    assert parents[2] == ids[1]
    # Exactly one branch, it is main, and it points at the newest revision.
    assert len(branch) == 1
    assert branch[0][0] is None
    assert branch[0][1] == ids[2]


def test_020_is_idempotent_when_the_chain_is_already_built():
    """Same reason as 019: the hook runs before every rollout, and a
    second run must not duplicate an article's entire history."""
    with PostgresContainer("postgres:16-alpine") as pg:
        url, engine = _db_at_019_with_an_edited_article(pg)
        _alembic(url, "upgrade", "head")
        _alembic(url, "stamp", "019")
        _alembic(url, "upgrade", "head")
        with engine.begin() as conn:
            count = conn.execute(sa.text(
                "SELECT count(*) FROM doc_revisions")).scalar()
            branches = conn.execute(sa.text(
                "SELECT count(*) FROM doc_branches")).scalar()
        engine.dispose()

    assert count == 3
    assert branches == 1


def test_021_creates_the_proposal_tables_with_their_rules():
    """The policy the editors chose lives in the schema, not in prose:
    one open proposal per editor per article, and only three states."""
    with PostgresContainer("postgres:16-alpine") as pg:
        url = pg.get_connection_url().replace(
            "postgresql+psycopg2://", "postgresql://")
        engine = sa.create_engine(url)
        _alembic(url, "upgrade", "head")
        inspector = sa.inspect(engine)
        tables = set(inspector.get_table_names())
        indexes = {i["name"] for i in inspector.get_indexes("merge_requests")}
        with engine.begin() as conn:
            states = conn.execute(sa.text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'ck_merge_requests_state'")).scalar()
        engine.dispose()

    assert {"merge_requests", "mr_comments"} <= tables
    assert "uq_merge_requests_open" in indexes
    assert "open" in states and "merged" in states and "closed" in states
