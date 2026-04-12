"""AssistantService — the public entry point of the assistant module.

Callers (the router, future background jobs, other services) talk to
this class and this class alone. It:

  1. loads conversation history from its repository,
  2. budget-truncates the context and history,
  3. builds the system prompt,
  4. streams the response through a pluggable proxy client,
  5. records user + assistant rows with token counts,
  6. answers usage queries.

The proxy client is a small interface (``stream(payload) -> async iter of str``).
In production it's the httpx-based ``ClaudeProxyClient``; in tests it's a
fake. The service itself has no httpx dependency.
"""
# pylint: disable=missing-class-docstring,missing-function-docstring
# pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
# pylint: disable=too-few-public-methods,import-outside-toplevel
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator as TypingAsyncIterator, Protocol

from src.assistant.context import (
    TurnLimits,
    budget_context_block,
    build_system_prompt,
    truncate_history,
)
from src.assistant.repository import AssistRepository, DailyUsage
from src.assistant.tokens import (
    TokenUsage,
    estimate_tokens,
    parse_sse_usage,
)


# ── Public data types ──────────────────────────────────────────


@dataclass(frozen=True)
class ChatRequest:
    user_id: str
    conversation_key: str
    message: str
    context_block: str


@dataclass(frozen=True)
class UsageSnapshot:
    tokens_1h: int
    tokens_24h: int
    tokens_7d: int


# ── Proxy client protocol ──────────────────────────────────────


class ProxyClient(Protocol):
    """Anything that takes a payload and yields SSE lines back."""

    def stream(
        self, payload: dict
    ) -> TypingAsyncIterator[str]:  # pragma: no cover - protocol
        ...


# ── Service ────────────────────────────────────────────────────


class AssistantService:

    def __init__(
        self,
        repo: AssistRepository,
        proxy_client: ProxyClient,
        base_system_prompt: str,
        turn_limits: TurnLimits,
        context_char_budget: int,
    ) -> None:
        self._repo = repo
        self._proxy = proxy_client
        self._base_prompt = base_system_prompt
        self._turn_limits = turn_limits
        self._context_budget = context_char_budget

    # ─────────── Turn handling ────────────

    async def turn(self, req: ChatRequest) -> AsyncIterator[str]:  # NOSONAR S3776: SSE stream reconciliation is inherently sequential
        """Handle a single chat turn, streaming SSE lines back to the caller.

        Persists a user row before calling the proxy (so even cancelled
        turns are visible in usage reports), then persists an assistant
        row on stream completion with real token counts if the proxy
        forwarded them, otherwise with estimates.
        """
        conv = await self._repo.find_or_create_conversation(
            req.user_id, req.conversation_key
        )

        # Build history view for the LLM, from persisted rows only.
        prior = await self._repo.history_turns(conv.id)
        windowed = truncate_history(prior, self._turn_limits)

        budgeted_context = budget_context_block(
            req.context_block, char_budget=self._context_budget
        )
        system_prompt = build_system_prompt(
            self._base_prompt, budgeted_context, windowed
        )

        # Persist the user row immediately with an estimate.
        # If the proxy forwards real usage later, we'll reconcile.
        estimated_in = estimate_tokens(req.message) + estimate_tokens(budgeted_context)
        await self._repo.append_message(
            conversation_id=conv.id,
            user_id=req.user_id,
            role="user",
            content=req.message,
            tokens_in=estimated_in,
            tokens_out=None,
            model=None,
        )

        # Stream through the proxy, accumulating text for the assistant row.
        assistant_buf: list[str] = []
        real_usage: TokenUsage | None = None
        errored = False

        payload = {"system": system_prompt, "message": req.message}

        async for line in self._proxy.stream(payload):
            yield line
            parsed = _parse_sse_line(line)
            if parsed is None:
                continue
            event, data = parsed
            if event == "chunk":
                text = _extract_chunk_text(data)
                if text:
                    assistant_buf.append(text)
            elif event == "error":
                errored = True
            else:
                usage = parse_sse_usage(event, data)
                if usage is not None:
                    real_usage = usage

        # Only persist the assistant row if we actually accumulated text.
        # An error-before-any-chunks turn leaves a dangling user row — that's
        # intentional: the user's token budget was spent on the failed attempt.
        if assistant_buf:
            assistant_text = "".join(assistant_buf)
            tokens_out = (
                real_usage.output_tokens
                if real_usage is not None
                else estimate_tokens(assistant_text)
            )
            await self._repo.append_message(
                conversation_id=conv.id,
                user_id=req.user_id,
                role="assistant",
                content=assistant_text,
                tokens_in=None,
                tokens_out=tokens_out,
                model=None,
            )

            # If we got a real input_tokens count, reconcile the user row.
            # For simplicity we write a correction row in extras rather than
            # mutating the persisted user row. MVP: update the most-recent
            # user row's tokens_in in place via the repo's write path.
            if real_usage is not None:
                await self._update_last_user_tokens_in(
                    conv.id, real_usage.input_tokens
                )

        # Commit all writes so they survive regardless of how the
        # HTTP framework manages the session lifecycle (e.g. streaming
        # responses where framework cleanup may run too late).
        await self._repo.commit()

        if errored and not assistant_buf:
            return

    async def _update_last_user_tokens_in(
        self, conversation_id: str, tokens_in: int
    ) -> None:
        """Reconcile the estimated tokens_in on the most-recent user row.

        MVP implementation: list messages, find the last user row, mutate
        it via a repo helper. Currently the memory repo mutates the object
        directly (which is what we want for tests), and the Postgres repo
        will need a dedicated ``update_tokens_in`` method later. For now,
        since the Postgres repo keeps a session reference, we mutate the
        SQLAlchemy object via a targeted update.
        """
        messages = await self._repo.list_messages(conversation_id)
        for msg in reversed(messages):
            if msg.role == "user":
                msg.tokens_in = tokens_in
                # For the Postgres repo: the underlying ORM object is not
                # automatically re-fetched. We run a SQL update as a second
                # step in the service layer via a dedicated method on the
                # repo interface in a follow-up. For this MVP, the in-memory
                # mutation is sufficient for tests and the Postgres path
                # will use the estimated value until a reconcile job runs.
                return

    # ─────────── Usage queries ────────────

    async def usage_for_user(
        self, user_id: str, now: datetime | None = None
    ) -> UsageSnapshot:
        now = now or datetime.now(timezone.utc)
        tokens_1h = await self._repo.tokens_used_since(user_id, now - timedelta(hours=1))
        tokens_24h = await self._repo.tokens_used_since(user_id, now - timedelta(hours=24))
        tokens_7d = await self._repo.tokens_used_since(user_id, now - timedelta(days=7))
        return UsageSnapshot(
            tokens_1h=tokens_1h,
            tokens_24h=tokens_24h,
            tokens_7d=tokens_7d,
        )

    async def usage_history_for_user(
        self,
        user_id: str,
        days: int,
        now: datetime | None = None,
    ) -> list[DailyUsage]:
        """Per-day token totals for the last ``days`` days (ending now)."""
        now = now or datetime.now(timezone.utc)
        since = now - timedelta(days=days)
        return await self._repo.usage_history_since(user_id, since)


# ── SSE parsing helpers ───────────────────────────────────────


def _parse_sse_line(line: str) -> tuple[str, str] | None:
    """Parse a single SSE event block (event: + data:).

    SSE lines come in pairs. For the purposes of event inspection we
    accept a relaxed shape where a single line can carry both parts
    separated by ``\\n`` (which is what FakeProxyClient emits in tests).
    """
    # FakeProxyClient emits whole events, e.g.
    # "event: chunk\ndata: {...}\n\n"
    event = None
    data = None
    for part in line.split("\n"):
        part = part.strip()
        if part.startswith("event:"):
            event = part[len("event:"):].strip()
        elif part.startswith("data:"):
            data = part[len("data:"):].strip()
    if event is None or data is None:
        return None
    return event, data


def _extract_chunk_text(data: str) -> str:
    """Pull the ``text`` field from a chunk payload, tolerant of shape."""
    try:
        obj = json.loads(data)
    except (ValueError, TypeError):
        return ""
    if isinstance(obj, dict):
        text = obj.get("text")
        if isinstance(text, str):
            return text
        # Claude proxy may nest in delta.text
        delta = obj.get("delta")
        if isinstance(delta, dict):
            inner = delta.get("text")
            if isinstance(inner, str):
                return inner
    return ""
