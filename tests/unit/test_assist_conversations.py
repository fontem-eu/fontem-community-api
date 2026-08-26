"""Many conversations, each its own topic.

The storage always allowed this: assist_conversations is unique on
(user_id, conversation_key) and the key is documented as opaque. What was
missing was any way to enumerate, name or remove one — the API could fetch a
conversation by key and delete every conversation the user had, and nothing
in between.

Two behaviours here are about damage rather than features. A conversation is
scoped to its owner, so one user's key must never reach another's rows. And
deleting one must delete one: the blanket delete stays, but it is no longer
what a delete button calls.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.assistant.repository import InMemoryAssistRepository
from src.assistant.router import STANDALONE_PREFIX, _auto_title


BASE = datetime(2026, 8, 25, 10, 0, tzinfo=timezone.utc)


async def _say(repo, conv_id, user_id, role, content, when):
    repo._now = lambda: when          # pylint: disable=protected-access
    return await repo.append_message(
        conversation_id=conv_id, user_id=user_id, role=role,
        content=content, tokens_in=None, tokens_out=None, model=None,
    )


@pytest.fixture(name="repo")
def _repo():
    return InMemoryAssistRepository()


@pytest.mark.asyncio
async def test_conversations_are_listed_newest_activity_first(repo):
    older = await repo.find_or_create_conversation("u1", "chat:a")
    newer = await repo.find_or_create_conversation("u1", "chat:b")
    await _say(repo, older.id, "u1", "user", "old question", BASE)
    await _say(repo, newer.id, "u1", "user", "new question", BASE + timedelta(hours=1))
    keys = [c.conversation_key for c in await repo.list_conversations("u1")]
    assert keys == ["chat:b", "chat:a"]


@pytest.mark.asyncio
async def test_a_listing_carries_what_a_switcher_needs(repo):
    conv = await repo.find_or_create_conversation("u1", "chat:a")
    await _say(repo, conv.id, "u1", "user", "who supplies Russia?", BASE)
    await _say(repo, conv.id, "u1", "assistant", "Seven companies.", BASE + timedelta(seconds=5))
    listed = (await repo.list_conversations("u1"))[0]
    assert listed.message_count == 2
    assert listed.last_snippet == "Seven companies."


@pytest.mark.asyncio
async def test_the_snippet_is_the_last_thing_said_not_the_last_row(repo):
    """A tool call is the agent's bookkeeping. A switcher showing
    'mcp__gmr__search_entities' tells the reader nothing about which
    conversation this is."""
    conv = await repo.find_or_create_conversation("u1", "chat:a")
    await _say(repo, conv.id, "u1", "user", "who supplies Russia?", BASE)
    await _say(repo, conv.id, "u1", "assistant", "Seven companies.", BASE + timedelta(seconds=5))
    await _say(repo, conv.id, "u1", "tool", "mcp__gmr__search_entities", BASE + timedelta(seconds=9))
    listed = (await repo.list_conversations("u1"))[0]
    assert listed.last_snippet == "Seven companies."
    assert listed.message_count == 3


@pytest.mark.asyncio
async def test_one_users_conversations_are_not_anothers(repo):
    mine = await repo.find_or_create_conversation("u1", "chat:mine")
    await repo.find_or_create_conversation("u2", "chat:theirs")
    await _say(repo, mine.id, "u1", "user", "mine", BASE)
    assert [c.conversation_key for c in await repo.list_conversations("u1")] == ["chat:mine"]
    assert [c.conversation_key for c in await repo.list_conversations("u2")] == ["chat:theirs"]


@pytest.mark.asyncio
async def test_renaming_sticks(repo):
    await repo.find_or_create_conversation("u1", "chat:a")
    assert await repo.rename_conversation("u1", "chat:a", "Hungarian contracts") is True
    assert (await repo.list_conversations("u1"))[0].title == "Hungarian contracts"


@pytest.mark.asyncio
async def test_renaming_someone_elses_conversation_does_nothing(repo):
    await repo.find_or_create_conversation("u2", "chat:theirs")
    assert await repo.rename_conversation("u1", "chat:theirs", "mine now") is False
    assert (await repo.list_conversations("u2"))[0].title is None


@pytest.mark.asyncio
async def test_deleting_one_leaves_the_others(repo):
    a = await repo.find_or_create_conversation("u1", "chat:a")
    b = await repo.find_or_create_conversation("u1", "chat:b")
    await _say(repo, a.id, "u1", "user", "in a", BASE)
    await _say(repo, b.id, "u1", "user", "in b", BASE)

    assert await repo.delete_conversation("u1", "chat:a") is True
    remaining = await repo.list_conversations("u1")
    assert [c.conversation_key for c in remaining] == ["chat:b"]
    assert (await repo.list_messages(b.id))[0].content == "in b"


@pytest.mark.asyncio
async def test_deleting_takes_its_messages_with_it(repo):
    conv = await repo.find_or_create_conversation("u1", "chat:a")
    await _say(repo, conv.id, "u1", "user", "in a", BASE)
    await repo.delete_conversation("u1", "chat:a")
    assert await repo.list_messages(conv.id) == []


@pytest.mark.asyncio
async def test_deleting_someone_elses_conversation_does_nothing(repo):
    theirs = await repo.find_or_create_conversation("u2", "chat:theirs")
    await _say(repo, theirs.id, "u2", "user", "private", BASE)
    assert await repo.delete_conversation("u1", "chat:theirs") is False
    assert len(await repo.list_messages(theirs.id)) == 1


# --- what the switcher shows -------------------------------------------------

def test_report_chats_are_not_standalone():
    """A report's chat belongs to that report and opens with it. Listing it
    in the switcher fills the sidebar with entries nobody chose to start."""
    assert "report:abc".startswith(STANDALONE_PREFIX) is False
    assert "chat:abc".startswith(STANDALONE_PREFIX) is True


@pytest.mark.parametrize("asked,expected", [
    ("How much has Mészáros won?", "How much has Mészáros won?"),
    ("   spaced   out   ", "spaced out"),
    ("", ""),
])
def test_a_title_comes_from_the_opening_question(asked, expected):
    assert _auto_title(asked) == expected


def test_a_long_title_is_cut_on_a_word_boundary():
    asked = "which Hungarian construction companies won the most public money last year"
    title = _auto_title(asked)
    assert len(title) <= 61          # 60 plus the ellipsis
    assert title.endswith("…")
    assert not title[:-1].endswith(" ")
    assert " " in title and title.split()[-1] != "…"
