"""Assistant repository — abstract interface + in-memory implementation.

The assistant module owns its own storage. Callers never read or write
chat history directly; they go through this interface. The Postgres
implementation lives in ``pg_repository.py``.
"""
# pylint: disable=missing-class-docstring,missing-function-docstring
# pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-instance-attributes
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Callable
from uuid import uuid4

from src.assistant.context import Turn


# ── Data classes (wire format for the repository layer) ────────


@dataclass(frozen=True)
class DailyUsage:
    """One day's worth of input/output token totals for a user."""
    day: date
    tokens_in: int
    tokens_out: int


@dataclass
class AssistConversation:
    id: str
    user_id: str
    conversation_key: str
    created_at: datetime
    updated_at: datetime
    #: What a switcher shows. None until the conversation has a first
    #: message to take a title from.
    title: str | None = None
    #: Populated by list_conversations; not stored.
    message_count: int = 0
    last_snippet: str = ""


@dataclass
class AssistMessage:
    id: str
    conversation_id: str
    user_id: str
    role: str           # "user" | "assistant"
    content: str
    extras: dict = field(default_factory=dict)
    tokens_in: int | None = None
    tokens_out: int | None = None
    model: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ── Abstract interface ────────────────────────────────────────


class AssistRepository(ABC):
    """The only surface the assistant service is allowed to touch."""

    @abstractmethod
    async def find_or_create_conversation(
        self, user_id: str, conversation_key: str
    ) -> AssistConversation:
        ...

    @abstractmethod
    async def append_message(
        self,
        conversation_id: str,
        user_id: str,
        role: str,
        content: str,
        tokens_in: int | None,
        tokens_out: int | None,
        model: str | None,
        extras: dict | None = None,
        message_id: str | None = None,
    ) -> AssistMessage:
        ...

    @abstractmethod
    async def set_tokens_in(self, message_id: str, tokens_in: int) -> None:
        """Correct a message's estimated tokens_in with the real count.

        Its own method because the obvious implementation is wrong: reading
        a message, assigning to it and hoping is a no-op against Postgres,
        where list_messages hands back detached dataclasses. That is exactly
        what production did, so every tokens_in ever stored was the estimate.
        """

    @abstractmethod
    async def get_message(self, message_id: str) -> AssistMessage | None:
        """One message by id, or None.

        Carries user_id and conversation_id, which is what lets the caller
        check ownership before showing anything: an activity entry names a
        message id, and a message id is guessable in exactly the way a
        conversation is not.
        """

    @abstractmethod
    async def list_messages(self, conversation_id: str) -> list[AssistMessage]:
        ...

    @abstractmethod
    async def list_conversations(self, user_id: str) -> list[AssistConversation]:
        """Every conversation this user has, newest activity first.

        Carries the counts and the last line so a switcher can be drawn from
        one call. Fetching each conversation to render a list of them is how
        a sidebar becomes slower than the thing it indexes.
        """

    @abstractmethod
    async def rename_conversation(
        self, user_id: str, conversation_key: str, title: str
    ) -> bool:
        """Set a title. False when the conversation is not this user's."""

    @abstractmethod
    async def delete_conversation(self, user_id: str, conversation_key: str) -> bool:
        """Delete one conversation and its messages. False when not theirs."""

    @abstractmethod
    async def page_messages(
        self,
        conversation_id: str,
        *,
        limit: int,
        before: tuple[datetime, str] | None = None,
    ) -> list[AssistMessage]:
        """The newest ``limit`` messages, oldest-first within the page.

        Keyset, not offset. Messages are appended constantly — a tool call
        lands mid-scroll — and an offset shifts underneath the reader, so
        page two silently repeats or skips a row. The cursor is
        ``(created_at, id)``: created_at alone is not unique, because a turn
        can write several tool rows inside the same clock tick.
        """

    @abstractmethod
    async def history_turns(self, conversation_id: str) -> list[Turn]:
        ...

    @abstractmethod
    async def tokens_used_since(self, user_id: str, since: datetime) -> int:
        ...

    @abstractmethod
    async def commit(self) -> None:
        """Flush pending writes to the underlying store.

        In-memory repos treat this as a no-op. The Postgres repo
        delegates to ``session.commit()``. Callers (the service) invoke
        this at the end of a streaming turn so data is persisted before
        the response finishes.
        """

    @abstractmethod
    async def delete_user_conversations(self, user_id: str) -> int:
        """Delete all conversations and messages for a user. Returns count deleted."""

    @abstractmethod
    async def usage_history_since(
        self, user_id: str, since: datetime
    ) -> list[DailyUsage]:
        """Per-day input/output token totals for a user, at or after ``since``.

        Days with no activity are omitted (callers can fill gaps). Results
        are ordered by day ascending.
        """


# ── In-memory implementation (for unit tests + dev) ───────────


class InMemoryAssistRepository(AssistRepository):
    """In-memory repository. Thread-unsafe on purpose — tests only."""

    def __init__(
        self,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self._now: Callable[[], datetime] = (
            now_provider or (lambda: datetime.now(timezone.utc))
        )
        self._conversations: dict[str, AssistConversation] = {}
        self._by_key: dict[tuple[str, str], str] = {}  # (user_id, key) -> conv_id
        self._messages: list[AssistMessage] = []

    async def find_or_create_conversation(
        self, user_id: str, conversation_key: str
    ) -> AssistConversation:
        existing_id = self._by_key.get((user_id, conversation_key))
        if existing_id is not None:
            return self._conversations[existing_id]
        now = self._now()
        conv = AssistConversation(
            id=str(uuid4()),
            user_id=user_id,
            conversation_key=conversation_key,
            created_at=now,
            updated_at=now,
        )
        self._conversations[conv.id] = conv
        self._by_key[(user_id, conversation_key)] = conv.id
        return conv

    async def append_message(
        self,
        conversation_id: str,
        user_id: str,
        role: str,
        content: str,
        tokens_in: int | None,
        tokens_out: int | None,
        model: str | None,
        extras: dict | None = None,
        message_id: str | None = None,
    ) -> AssistMessage:
        msg = AssistMessage(
            id=message_id or str(uuid4()),
            conversation_id=conversation_id,
            user_id=user_id,
            role=role,
            content=content,
            extras=extras or {},
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            model=model,
            created_at=self._now(),
        )
        self._messages.append(msg)
        # Bump conversation.updated_at so rate queries see freshness
        conv = self._conversations.get(conversation_id)
        if conv is not None:
            conv.updated_at = msg.created_at
        return msg

    async def set_tokens_in(self, message_id: str, tokens_in: int) -> None:
        for msg in self._messages:
            if msg.id == message_id:
                msg.tokens_in = tokens_in
                return

    async def get_message(self, message_id: str) -> AssistMessage | None:
        for msg in self._messages:
            if msg.id == message_id:
                return msg
        return None

    async def list_messages(self, conversation_id: str) -> list[AssistMessage]:
        return [
            m for m in self._messages
            if m.conversation_id == conversation_id
        ]

    async def list_conversations(self, user_id: str) -> list[AssistConversation]:
        out = []
        for conv in self._conversations.values():
            if conv.user_id != user_id:
                continue
            msgs = sorted(
                (m for m in self._messages if m.conversation_id == conv.id),
                key=lambda m: (m.created_at, m.id),
            )
            conv.message_count = len(msgs)
            # The last thing said, not the last thing stored: a tool row is
            # the agent's bookkeeping and tells the reader nothing about
            # which conversation this is.
            spoken = [m for m in msgs if m.role in ("user", "assistant")]
            conv.last_snippet = spoken[-1].content[:120] if spoken else ""
            out.append(conv)
        return sorted(out, key=lambda c: c.updated_at, reverse=True)

    async def rename_conversation(
        self, user_id: str, conversation_key: str, title: str
    ) -> bool:
        for conv in self._conversations.values():
            if conv.user_id == user_id and conv.conversation_key == conversation_key:
                conv.title = title
                return True
        return False

    async def delete_conversation(self, user_id: str, conversation_key: str) -> bool:
        for conv_id, conv in list(self._conversations.items()):
            if conv.user_id == user_id and conv.conversation_key == conversation_key:
                self._messages = [
                    m for m in self._messages if m.conversation_id != conv_id
                ]
                del self._conversations[conv_id]
                return True
        return False

    async def page_messages(
        self,
        conversation_id: str,
        *,
        limit: int,
        before: tuple[datetime, str] | None = None,
    ) -> list[AssistMessage]:
        rows = sorted(
            (m for m in self._messages if m.conversation_id == conversation_id),
            key=lambda m: (m.created_at, m.id),
        )
        if before is not None:
            rows = [m for m in rows if (m.created_at, m.id) < before]
        return rows[-limit:] if limit > 0 else []

    async def history_turns(self, conversation_id: str) -> list[Turn]:
        return [
            Turn(role=m.role, content=m.content)
            for m in await self.list_messages(conversation_id)
        ]

    async def commit(self) -> None:
        pass  # In-memory: writes are immediate

    async def tokens_used_since(self, user_id: str, since: datetime) -> int:
        total = 0
        for m in self._messages:
            if m.user_id != user_id:
                continue
            if m.created_at < since:
                continue
            total += (m.tokens_in or 0) + (m.tokens_out or 0)
        return total

    async def delete_user_conversations(self, user_id: str) -> int:
        conv_ids = {
            cid for cid, conv in self._conversations.items()
            if conv.user_id == user_id
        }
        count = len(conv_ids)
        self._messages = [
            m for m in self._messages
            if m.conversation_id not in conv_ids
        ]
        for cid in conv_ids:
            del self._conversations[cid]
        self._by_key = {
            k: v for k, v in self._by_key.items() if v not in conv_ids
        }
        return count

    async def usage_history_since(
        self, user_id: str, since: datetime
    ) -> list[DailyUsage]:
        buckets: dict[date, list[int]] = {}
        for m in self._messages:
            if m.user_id != user_id or m.created_at < since:
                continue
            bucket = buckets.setdefault(m.created_at.date(), [0, 0])
            bucket[0] += m.tokens_in or 0
            bucket[1] += m.tokens_out or 0
        return [
            DailyUsage(day=d, tokens_in=v[0], tokens_out=v[1])
            for d, v in sorted(buckets.items())
        ]
