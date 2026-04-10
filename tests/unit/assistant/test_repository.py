"""Repository contract tests for the in-memory impl.

These tests define the behavioural contract every repository
implementation (memory, Postgres) must honour. The Postgres impl gets
its own integration test that runs the same scenarios against a real
database, but the bulk of the coverage lives here.
"""
# pylint: disable=missing-class-docstring,missing-function-docstring,protected-access,unused-import,too-few-public-methods
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.assistant.context import Turn
from src.assistant.repository import (
    AssistConversation,
    AssistMessage,
    InMemoryAssistRepository,
)


NOW = datetime(2026, 4, 11, 12, 0, 0, tzinfo=timezone.utc)


def _clock(t: datetime = NOW):
    """Injectable clock for deterministic timestamps."""
    return lambda: t


# ── find_or_create_conversation ────────────────────────────────

@pytest.mark.asyncio
class TestFindOrCreateConversation:

    async def test_creates_new_conversation(self):
        repo = InMemoryAssistRepository(now_provider=_clock())
        conv = await repo.find_or_create_conversation(
            user_id="u1", conversation_key="report:abc"
        )
        assert conv.id
        assert conv.user_id == "u1"
        assert conv.conversation_key == "report:abc"

    async def test_returns_existing_on_same_key(self):
        repo = InMemoryAssistRepository(now_provider=_clock())
        first = await repo.find_or_create_conversation("u1", "report:abc")
        second = await repo.find_or_create_conversation("u1", "report:abc")
        assert first.id == second.id

    async def test_isolates_by_user(self):
        repo = InMemoryAssistRepository(now_provider=_clock())
        a = await repo.find_or_create_conversation("u1", "report:abc")
        b = await repo.find_or_create_conversation("u2", "report:abc")
        assert a.id != b.id


# ── append_message / list_messages ─────────────────────────────

@pytest.mark.asyncio
class TestMessageAppendAndList:

    async def test_append_and_list_in_order(self):
        repo = InMemoryAssistRepository(now_provider=_clock())
        conv = await repo.find_or_create_conversation("u1", "report:abc")

        await repo.append_message(
            conversation_id=conv.id, user_id="u1", role="user",
            content="hello", tokens_in=5, tokens_out=None, model=None,
        )
        await repo.append_message(
            conversation_id=conv.id, user_id="u1", role="assistant",
            content="hi there", tokens_in=None, tokens_out=7,
            model="claude-sonnet-4-6",
        )

        messages = await repo.list_messages(conv.id)
        assert len(messages) == 2
        assert messages[0].role == "user"
        assert messages[0].content == "hello"
        assert messages[0].tokens_in == 5
        assert messages[1].role == "assistant"
        assert messages[1].tokens_out == 7
        assert messages[1].model == "claude-sonnet-4-6"

    async def test_list_returns_empty_for_new_conversation(self):
        repo = InMemoryAssistRepository(now_provider=_clock())
        conv = await repo.find_or_create_conversation("u1", "report:abc")
        assert await repo.list_messages(conv.id) == []

    async def test_append_returns_the_created_row(self):
        repo = InMemoryAssistRepository(now_provider=_clock())
        conv = await repo.find_or_create_conversation("u1", "report:abc")
        msg = await repo.append_message(
            conversation_id=conv.id, user_id="u1", role="user",
            content="x", tokens_in=1, tokens_out=None, model=None,
        )
        assert msg.id
        assert msg.conversation_id == conv.id
        assert msg.created_at == NOW

    async def test_list_does_not_bleed_between_conversations(self):
        repo = InMemoryAssistRepository(now_provider=_clock())
        a = await repo.find_or_create_conversation("u1", "report:aaa")
        b = await repo.find_or_create_conversation("u1", "report:bbb")
        await repo.append_message(
            conversation_id=a.id, user_id="u1", role="user",
            content="in A", tokens_in=1, tokens_out=None, model=None,
        )
        msgs_a = await repo.list_messages(a.id)
        msgs_b = await repo.list_messages(b.id)
        assert len(msgs_a) == 1
        assert len(msgs_b) == 0


# ── tokens_used_since ──────────────────────────────────────────

@pytest.mark.asyncio
class TestTokensUsedSince:

    async def test_sums_user_tokens(self):
        repo = InMemoryAssistRepository(now_provider=_clock())
        conv = await repo.find_or_create_conversation("u1", "report:x")
        await repo.append_message(
            conversation_id=conv.id, user_id="u1", role="user",
            content="q", tokens_in=10, tokens_out=None, model=None,
        )
        await repo.append_message(
            conversation_id=conv.id, user_id="u1", role="assistant",
            content="a", tokens_in=None, tokens_out=20, model="x",
        )
        since = NOW - timedelta(hours=1)
        assert await repo.tokens_used_since("u1", since) == 30

    async def test_isolates_by_user(self):
        repo = InMemoryAssistRepository(now_provider=_clock())
        c1 = await repo.find_or_create_conversation("u1", "k1")
        c2 = await repo.find_or_create_conversation("u2", "k2")
        await repo.append_message(
            conversation_id=c1.id, user_id="u1", role="user",
            content="x", tokens_in=100, tokens_out=None, model=None,
        )
        await repo.append_message(
            conversation_id=c2.id, user_id="u2", role="user",
            content="y", tokens_in=999, tokens_out=None, model=None,
        )
        since = NOW - timedelta(hours=1)
        assert await repo.tokens_used_since("u1", since) == 100
        assert await repo.tokens_used_since("u2", since) == 999

    async def test_respects_since_window(self):
        # Messages older than `since` are excluded
        old_clock = _clock(NOW - timedelta(days=2))
        repo = InMemoryAssistRepository(now_provider=old_clock)
        conv = await repo.find_or_create_conversation("u1", "k")
        await repo.append_message(
            conversation_id=conv.id, user_id="u1", role="user",
            content="old", tokens_in=500, tokens_out=None, model=None,
        )
        # Move the clock forward and add a recent message
        repo._now = _clock()  # pylint: disable=protected-access
        await repo.append_message(
            conversation_id=conv.id, user_id="u1", role="user",
            content="new", tokens_in=10, tokens_out=None, model=None,
        )

        # 24-hour window: only "new" counts
        one_day_ago = NOW - timedelta(hours=24)
        assert await repo.tokens_used_since("u1", one_day_ago) == 10

        # 7-day window: both count
        seven_days_ago = NOW - timedelta(days=7)
        assert await repo.tokens_used_since("u1", seven_days_ago) == 510

    async def test_zero_when_no_messages(self):
        repo = InMemoryAssistRepository(now_provider=_clock())
        since = NOW - timedelta(hours=1)
        assert await repo.tokens_used_since("ghost", since) == 0

    async def test_ignores_null_token_counts(self):
        # A dangling user row with no matching assistant row (client
        # disconnect) should still sum its tokens_in.
        repo = InMemoryAssistRepository(now_provider=_clock())
        conv = await repo.find_or_create_conversation("u1", "k")
        await repo.append_message(
            conversation_id=conv.id, user_id="u1", role="user",
            content="dangling", tokens_in=42, tokens_out=None, model=None,
        )
        since = NOW - timedelta(hours=1)
        assert await repo.tokens_used_since("u1", since) == 42


# ── to_history_turns (helper for history reconstruction) ───────

@pytest.mark.asyncio
class TestToHistoryTurns:

    async def test_converts_messages_to_turns(self):
        repo = InMemoryAssistRepository(now_provider=_clock())
        conv = await repo.find_or_create_conversation("u1", "k")
        await repo.append_message(
            conversation_id=conv.id, user_id="u1", role="user",
            content="hi", tokens_in=1, tokens_out=None, model=None,
        )
        await repo.append_message(
            conversation_id=conv.id, user_id="u1", role="assistant",
            content="hello", tokens_in=None, tokens_out=1, model="x",
        )

        turns = await repo.history_turns(conv.id)
        assert turns == [
            Turn(role="user", content="hi"),
            Turn(role="assistant", content="hello"),
        ]
