"""The directory SQL, against a real Postgres.

Three grouped aggregates outer-joined onto users, ordered NULLS LAST with an
id tiebreak: exactly the query whose in-memory twin can agree with every unit
test while the SQL quietly multiplies one join by another. Account A below
has three activities, two sessions and two roles — a naive join would count
twelve.
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

from src.infra.postgres.pg_user_directory_repo import PgUserDirectoryRepository
from src.infra.postgres.pg_user_repo import PgUserRepository

NOW = datetime.now(timezone.utc).replace(microsecond=0)
A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
C = "cccccccc-cccc-cccc-cccc-cccccccccccc"


def _alembic(url: str, *args: str) -> None:
    env = {**os.environ,
           "DATABASE_URL": url.replace("postgresql://", "postgresql+asyncpg://")}
    proc = subprocess.run([sys.executable, "-m", "alembic", *args],
                          env=env, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"alembic {' '.join(args)} failed:\n{proc.stderr}"


@pytest.fixture(scope="module")
def seeded():
    with PostgresContainer("postgres:16-alpine") as pg:
        url = pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")
        engine = sa.create_engine(url)
        with engine.begin() as conn:
            conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        engine.dispose()
        _alembic(url, "upgrade", "head")

        sync = sa.create_engine(url)
        with sync.begin() as conn:
            for uid, created, verified, login in (
                (A, NOW - timedelta(days=30), NOW - timedelta(days=29), NOW - timedelta(days=1)),
                (B, NOW - timedelta(days=20), None, None),
                (C, NOW - timedelta(days=10), NOW - timedelta(days=9), None),
            ):
                conn.execute(sa.text(
                    "INSERT INTO users (id, email, name, trust_level, created_at, "
                    "failed_login_attempts, email_verified_at, last_login_at) "
                    "VALUES (:id, :email, :name, 'contributor', :created, 0, :verified, :login)"),
                    {"id": uid, "email": f"{uid[:1]}@test.com", "name": uid[:1],
                     "created": created, "verified": verified, "login": login})
            for role in ("moderator", "admin"):
                conn.execute(sa.text("INSERT INTO user_roles (user_id, role) VALUES (:u, :r)"),
                             {"u": A, "r": role})
            for uid, days in ((A, 5), (A, 3), (A, 2), (B, 4)):
                conn.execute(sa.text(
                    "INSERT INTO activity_log (id, actor_id, entity_type, entity_id, action, "
                    "summary, created_at, actor_kind) VALUES (gen_random_uuid(), :u, 'story', "
                    "'s', 'updated', 's', :t, 'user')"),
                    {"u": uid, "t": NOW - timedelta(days=days)})
            for uid, hours in ((A, 6), (A, 2), (B, 72)):
                conn.execute(sa.text(
                    "INSERT INTO refresh_token_families (id, user_id, current_token_hash, "
                    "rotated_at, expires_at) VALUES (gen_random_uuid(), :u, md5(random()::text), "
                    ":t, :e)"),
                    {"u": uid, "t": NOW - timedelta(hours=hours), "e": NOW + timedelta(days=14)})
        sync.dispose()
        yield url.replace("postgresql://", "postgresql+asyncpg://")


@pytest.fixture()
def repo(seeded):
    engine = create_async_engine(seeded)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    loop = asyncio.new_event_loop()
    session = loop.run_until_complete(asyncio.sleep(0, factory()))

    def run(coro):
        return loop.run_until_complete(coro)

    yield run, PgUserDirectoryRepository(session), PgUserRepository(session)
    loop.run_until_complete(session.close())
    loop.run_until_complete(engine.dispose())
    loop.close()


def _page(run, directory, sort="registered", limit=50, offset=0):
    return run(directory.list_users(sort=sort, limit=limit, offset=offset))


def test_counts_are_not_multiplied_by_the_other_joins(repo):
    run, directory, _ = repo
    entries, total = _page(run, directory)
    account_a = next(entry for entry in entries if entry.id == A)

    assert total == 3
    assert account_a.activity_count == 3
    assert account_a.roles == ["admin", "moderator"]
    assert account_a.last_activity_at == NOW - timedelta(days=2)
    assert account_a.last_seen_at == NOW - timedelta(hours=2)


def test_an_account_with_no_activity_or_sessions_reads_zero_and_none(repo):
    run, directory, _ = repo
    account_c = next(entry for entry in _page(run, directory)[0] if entry.id == C)

    assert account_c.activity_count == 0
    assert account_c.last_seen_at is None
    assert account_c.last_activity_at is None
    assert account_c.roles == []


def test_email_verified_follows_the_timestamp(repo):
    run, directory, _ = repo
    by_id = {entry.id: entry for entry in _page(run, directory)[0]}
    assert by_id[A].email_verified is True
    assert by_id[B].email_verified is False


def test_every_order_is_descending_with_missing_values_last(repo):
    run, directory, _ = repo

    def order(sort):
        return [entry.id for entry in _page(run, directory, sort=sort)[0]]

    assert order("registered") == [C, B, A]
    assert order("last_login") == [A, B, C]      # B and C never signed in: last, by id
    assert order("last_seen") == [A, B, C]       # C has no session
    assert order("last_activity") == [A, B, C]   # A two days ago, B four; C never
    assert order("activity_count") == [A, B, C]  # 3, 1, 0


def test_pages_neither_overlap_nor_skip(repo):
    run, directory, _ = repo
    first, _ = _page(run, directory, limit=2, offset=0)
    second, total = _page(run, directory, limit=2, offset=2)

    assert [entry.id for entry in first + second] == [C, B, A]
    assert total == 3


def test_record_login_round_trips(repo):
    # Last, because it changes C's last login for anything run after it.
    run, _, users = repo
    when = NOW - timedelta(minutes=5)
    run(users.record_login(C, when))
    assert run(users.get_by_id(C)).last_login_at == when
