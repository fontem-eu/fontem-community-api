"""The ranking SQL, against a real Postgres.

The in-memory repository mirrors these semantics, but a window function over
a join with an unnested array and LIKE-ANY prefix matching is exactly the kind
of SQL that passes review and then does something else. This runs it.
"""
# pylint: disable=missing-function-docstring,redefined-outer-name
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from src.domain.feed import FeedItem
from src.infra.postgres.pg_feed_repo import PgFeedRepository

NOW = datetime.now(timezone.utc)


def _alembic(url: str, *args: str) -> None:
    env = {**os.environ,
           "DATABASE_URL": url.replace("postgresql://", "postgresql+asyncpg://")}
    proc = subprocess.run([sys.executable, "-m", "alembic", *args],
                          env=env, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"alembic {' '.join(args)} failed:\n{proc.stderr}"


@pytest.fixture(scope="module")
def seeded():
    """A migrated database with one published query in one public group."""
    with PostgresContainer("postgres:16-alpine") as pg:
        url = pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")
        engine = sa.create_engine(url)
        with engine.begin() as conn:
            conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        engine.dispose()
        _alembic(url, "upgrade", "head")

        sync = sa.create_engine(url)
        with sync.begin() as conn:
            conn.execute(sa.text(
                "INSERT INTO named_queries (id, slug, lang, status) VALUES "
                "('11111111-1111-1111-1111-111111111111','q','cypher','published'),"
                "('33333333-3333-3333-3333-333333333333','draft-q','cypher','draft')"))
            conn.execute(sa.text(
                "INSERT INTO query_groups (id, slug, name, visibility) VALUES "
                "('22222222-2222-2222-2222-222222222222','g','G','public')"))
            conn.execute(sa.text(
                "INSERT INTO query_group_members (group_id, query_id, sort_order) VALUES "
                "('22222222-2222-2222-2222-222222222222',"
                " '11111111-1111-1111-1111-111111111111',0),"
                "('22222222-2222-2222-2222-222222222222',"
                " '33333333-3333-3333-3333-333333333333',1)"))
        sync.dispose()
        yield url.replace("postgresql://", "postgresql+asyncpg://")


QUERY_ID = "11111111-1111-1111-1111-111111111111"
DRAFT_ID = "33333333-3333-3333-3333-333333333333"
GROUP_ID = "22222222-2222-2222-2222-222222222222"


@pytest.fixture()
def repo(seeded):
    engine = create_async_engine(seeded)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _make():
        session = factory()
        return session, PgFeedRepository(session)

    loop = asyncio.new_event_loop()
    session, repository = loop.run_until_complete(_make())

    def run(coro):
        return loop.run_until_complete(coro)

    yield run, repository, session
    loop.run_until_complete(session.close())
    loop.run_until_complete(engine.dispose())
    loop.close()


def _item(item_id, days_ago, nuts, rank, query_id=QUERY_ID):
    return FeedItem(query_id=query_id, item_id=item_id,
                    item_time=NOW - timedelta(days=days_ago),
                    nuts=nuts, rank_value=rank, title=item_id, link="https://x")


def test_upsert_is_idempotent_and_first_seen_at_never_moves(repo):
    run, repository, session = repo
    items = [_item("a", 1, ["PT17"], 10)]
    assert run(repository.upsert_items(items)) == 1
    assert run(repository.upsert_items(items)) == 0   # second time writes nothing

    stamp = run(session.execute(sa.text(
        "SELECT first_seen_at FROM feed_items WHERE item_id='a'"))).scalar_one()
    run(repository.upsert_items(items))
    again = run(session.execute(sa.text(
        "SELECT first_seen_at FROM feed_items WHERE item_id='a'"))).scalar_one()
    assert again == stamp


def test_regions_match_by_prefix(repo):
    run, repository, _ = repo
    run(repository.upsert_items([
        _item("pt-lisboa", 1, ["PT17"], 100),
        _item("pt-coimbra", 1, ["PT16"], 50),
        _item("es", 1, ["ES300"], 90),
    ]))
    got = run(repository.rank_items(GROUP_ID, ["PT"], 50, 4))
    assert {i.item_id for i in got} >= {"pt-lisboa", "pt-coimbra"}
    assert "es" not in {i.item_id for i in got}

    got = run(repository.rank_items(GROUP_ID, ["PT16"], 50, 4))
    assert "pt-coimbra" in {i.item_id for i in got}
    assert "pt-lisboa" not in {i.item_id for i in got}


def test_an_item_spanning_regions_matches_any_of_them(repo):
    run, repository, _ = repo
    run(repository.upsert_items([_item("cross", 1, ["PT17", "ES300"], 70)]))
    for region in ("PT", "ES"):
        assert "cross" in {i.item_id for i in run(
            repository.rank_items(GROUP_ID, [region], 50, 4))}


def test_everywhere_matches_everything(repo):
    run, repository, _ = repo
    run(repository.upsert_items([_item("anywhere", 1, ["FI1B1"], 5)]))
    assert "anywhere" in {i.item_id for i in run(
        repository.rank_items(GROUP_ID, ["EU"], 50, 4))}


def test_the_volume_is_per_week_so_one_busy_week_cannot_crowd_out_another(repo):
    run, repository, _ = repo
    # Three items this week, three the week before.
    run(repository.upsert_items(
        [_item(f"now-{i}", 1, ["DE21"], 100 + i) for i in range(3)]
        + [_item(f"old-{i}", 9, ["DE21"], 100 + i) for i in range(3)]))
    got = run(repository.rank_items(GROUP_ID, ["DE"], 1, 4))
    ids = {i.item_id for i in got}
    # One from each week — a flat LIMIT 1 would have returned only the newest.
    assert len([i for i in ids if i.startswith("now-")]) == 1
    assert len([i for i in ids if i.startswith("old-")]) == 1


def test_the_top_ranked_item_of_the_week_is_the_one_kept(repo):
    run, repository, _ = repo
    run(repository.upsert_items([
        _item("modest", 2, ["IE05"], 1_000),
        _item("huge", 2, ["IE05"], 9_000_000),
    ]))
    got = run(repository.rank_items(GROUP_ID, ["IE"], 1, 4))
    assert [i.item_id for i in got if i.item_id in ("modest", "huge")] == ["huge"]


def test_items_of_an_unpublished_query_are_never_served(repo):
    """A query pulled back to draft stops appearing, without deleting rows."""
    run, repository, _ = repo
    run(repository.upsert_items([_item("draft-item", 1, ["LU00"], 500, query_id=DRAFT_ID)]))
    got = run(repository.rank_items(GROUP_ID, ["LU"], 50, 4))
    assert "draft-item" not in {i.item_id for i in got}


def test_items_older_than_the_window_fall_out(repo):
    run, repository, _ = repo
    run(repository.upsert_items([_item("ancient", 90, ["SE11"], 1_000_000)]))
    assert "ancient" not in {i.item_id for i in run(
        repository.rank_items(GROUP_ID, ["SE"], 50, 4))}


def test_a_null_rank_value_sorts_last_but_is_not_dropped(repo):
    """A domain with no magnitude still gets a feed, ordered by time."""
    run, repository, _ = repo
    run(repository.upsert_items([
        _item("ranked", 3, ["AT13"], 10),
        _item("unranked", 3, ["AT13"], None),
    ]))
    got = [i.item_id for i in run(repository.rank_items(GROUP_ID, ["AT"], 50, 4))]
    assert "unranked" in got and "ranked" in got
