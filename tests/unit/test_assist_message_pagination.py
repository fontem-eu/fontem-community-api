"""Paging a conversation instead of fetching all of it.

`GET /assist/conversations/{key}` returns every message a conversation has
ever held. That is right for provenance tooling and wrong for a panel someone
opens twenty times a day: the cost of opening it grows without bound for the
life of the conversation.

The paging is keyset rather than offset, and these tests are mostly about why.
Messages are appended while the reader is scrolling — a turn writes a user row,
several tool rows and an assistant row — so an offset shifts underneath them
and page two repeats or skips. The cursor is `(created_at, id)`, because
created_at alone is not unique: tool rows from one turn routinely share a
timestamp.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.assistant.repository import InMemoryAssistRepository
from src.assistant.router import _decode_cursor, _encode_cursor


BASE = datetime(2026, 8, 25, 10, 0, tzinfo=timezone.utc)


async def _append(repo, conv_id, role, content, when):
    """Append at a controlled time.

    The repository stamps created_at from its own clock, so the clock is what
    gets steered — the alternative is writing rows and then reaching in to
    rewrite the field, which would test a shape the repository never produces.
    """
    repo._now = lambda: when          # pylint: disable=protected-access
    return await repo.append_message(
        conversation_id=conv_id, user_id="u1", role=role,
        content=content, tokens_in=None, tokens_out=None, model=None,
    )


async def _seed(repo, conv_id, count, *, same_tick=0):
    """`count` messages one second apart, plus `same_tick` sharing one stamp."""
    for i in range(count):
        await _append(repo, conv_id, "user", f"m{i}", BASE + timedelta(seconds=i))
    for j in range(same_tick):
        await _append(repo, conv_id, "tool", f"tool{j}", BASE + timedelta(seconds=count))


@pytest.fixture(name="repo")
def _repo():
    return InMemoryAssistRepository()


@pytest.mark.asyncio
async def test_a_page_is_the_newest_messages(repo):
    conv = await repo.find_or_create_conversation("u1", "chat:1")
    await _seed(repo, conv.id, 10)
    page = await repo.page_messages(conv.id, limit=3)
    assert [m.content for m in page] == ["m7", "m8", "m9"]


@pytest.mark.asyncio
async def test_a_page_is_oldest_first_within_itself(repo):
    """The panel renders top-to-bottom; a reversed page would read backwards."""
    conv = await repo.find_or_create_conversation("u1", "chat:1")
    await _seed(repo, conv.id, 10)
    page = await repo.page_messages(conv.id, limit=4)
    stamps = [m.created_at for m in page]
    assert stamps == sorted(stamps)


@pytest.mark.asyncio
async def test_the_next_page_continues_without_gaps_or_repeats(repo):
    conv = await repo.find_or_create_conversation("u1", "chat:1")
    await _seed(repo, conv.id, 10)
    first = await repo.page_messages(conv.id, limit=4)
    cursor = (first[0].created_at, first[0].id)
    second = await repo.page_messages(conv.id, limit=4, before=cursor)
    assert [m.content for m in second] == ["m2", "m3", "m4", "m5"]
    assert not {m.id for m in first} & {m.id for m in second}


@pytest.mark.asyncio
async def test_paging_is_stable_when_messages_arrive_mid_scroll(repo):
    """The whole reason this is keyset and not offset.

    The reader takes a page, a turn lands, and they scroll for the next one.
    With an offset the window has shifted by however many rows arrived and
    they silently re-read what they have already seen.
    """
    conv = await repo.find_or_create_conversation("u1", "chat:1")
    await _seed(repo, conv.id, 10)
    first = await repo.page_messages(conv.id, limit=4)
    cursor = (first[0].created_at, first[0].id)

    # a turn lands while the reader is scrolling
    for k in range(3):
        await _append(repo, conv.id, "assistant", f"new{k}",
                      BASE + timedelta(minutes=5, seconds=k))

    second = await repo.page_messages(conv.id, limit=4, before=cursor)
    assert [m.content for m in second] == ["m2", "m3", "m4", "m5"]


@pytest.mark.asyncio
async def test_messages_sharing_a_timestamp_do_not_break_the_boundary(repo):
    """created_at alone is not a key: one turn writes several tool rows in
    the same tick, and a cursor on the timestamp would skip or repeat them."""
    conv = await repo.find_or_create_conversation("u1", "chat:1")
    await _seed(repo, conv.id, 2, same_tick=5)
    seen, cursor, guard = [], None, 0
    while guard < 10:
        page = await repo.page_messages(conv.id, limit=2, before=cursor)
        if not page:
            break
        seen = [m.id for m in page] + seen
        cursor = (page[0].created_at, page[0].id)
        guard += 1
    assert len(seen) == 7, seen
    assert len(set(seen)) == 7, "a message was returned twice"


@pytest.mark.asyncio
async def test_an_empty_conversation_pages_to_nothing(repo):
    conv = await repo.find_or_create_conversation("u1", "chat:1")
    assert await repo.page_messages(conv.id, limit=10) == []


@pytest.mark.asyncio
async def test_a_page_never_leaks_another_conversation(repo):
    mine = await repo.find_or_create_conversation("u1", "chat:mine")
    theirs = await repo.find_or_create_conversation("u1", "chat:theirs")
    await _seed(repo, mine.id, 3)
    await _seed(repo, theirs.id, 3)
    page = await repo.page_messages(mine.id, limit=10)
    assert {m.conversation_id for m in page} == {mine.id}


# --- the cursor itself ------------------------------------------------------

def test_cursor_round_trips():
    encoded = _encode_cursor({"created_at": BASE.isoformat(), "id": "m1"})
    assert _decode_cursor(encoded) == (BASE, "m1")


@pytest.mark.parametrize("bad", ["", "nonsense", "|", "not-a-date|m1", "2026-08-25T10:00:00+00:00|"])
def test_an_unusable_cursor_reads_as_the_newest_page(bad):
    """Not a 422. It is an opaque token we issued; when it comes back wrong
    the useful answer is the start of the list, not an error the UI cannot
    act on."""
    assert _decode_cursor(bad) is None
