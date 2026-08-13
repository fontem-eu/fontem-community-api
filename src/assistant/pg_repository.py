"""PostgreSQL implementation of the assistant repository.

Mirrors the in-memory contract test-for-test. Any change to the
``AssistRepository`` interface must be reflected here *and* the
contract tests extended to catch the regression.
"""
# pylint: disable=missing-class-docstring,too-many-arguments,too-many-positional-arguments
from __future__ import annotations

from datetime import datetime

from sqlalchemy import and_, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.assistant.context import Turn
from src.assistant.models import AssistConversationModel, AssistMessageModel
from src.infra.postgres.models import ActivityLogModel
from src.assistant.repository import (
    AssistConversation,
    AssistMessage,
    AssistRepository,
    DailyUsage,
)


def _to_conv_dc(row: AssistConversationModel) -> AssistConversation:
    return AssistConversation(
        id=row.id,
        user_id=row.user_id,
        conversation_key=row.conversation_key,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_msg_dc(row: AssistMessageModel) -> AssistMessage:
    return AssistMessage(
        id=row.id,
        conversation_id=row.conversation_id,
        user_id=row.user_id,
        role=row.role,
        content=row.content,
        extras=row.extras or {},
        tokens_in=row.tokens_in,
        tokens_out=row.tokens_out,
        model=row.model,
        created_at=row.created_at,
    )


class PgAssistRepository(AssistRepository):

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_or_create_conversation(
        self, user_id: str, conversation_key: str
    ) -> AssistConversation:
        stmt = select(AssistConversationModel).where(
            and_(
                AssistConversationModel.user_id == user_id,
                AssistConversationModel.conversation_key == conversation_key,
            )
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is not None:
            return _to_conv_dc(row)
        row = AssistConversationModel(
            user_id=user_id, conversation_key=conversation_key,
        )
        self._session.add(row)
        await self._session.flush()
        return _to_conv_dc(row)

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
        row = AssistMessageModel(
            conversation_id=conversation_id,
            user_id=user_id,
            role=role,
            content=content,
            extras=extras or {},
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            model=model,
        )
        if message_id:
            # Minted where the tool ran, so the audit row a tool wrote and
            # this conversation row name the same call.
            row.id = message_id
        self._session.add(row)
        await self._session.flush()
        return _to_msg_dc(row)

    async def set_tokens_in(self, message_id: str, tokens_in: int) -> None:
        """An UPDATE, not a mutation.

        The previous implementation read the message and assigned to it,
        which works against the in-memory repo and does nothing here:
        list_messages returns detached dataclasses. Production therefore
        stored the character-count estimate on every row it ever wrote,
        while the tests were green.
        """
        await self._session.execute(
            update(AssistMessageModel)
            .where(AssistMessageModel.id == message_id)
            .values(tokens_in=tokens_in)
        )

    async def list_messages(self, conversation_id: str) -> list[AssistMessage]:
        stmt = (
            select(AssistMessageModel)
            .where(AssistMessageModel.conversation_id == conversation_id)
            .order_by(AssistMessageModel.created_at.asc())
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_msg_dc(r) for r in rows]

    async def history_turns(self, conversation_id: str) -> list[Turn]:
        messages = await self.list_messages(conversation_id)
        return [Turn(role=m.role, content=m.content) for m in messages]

    async def commit(self) -> None:
        await self._session.commit()

    async def tokens_used_since(self, user_id: str, since: datetime) -> int:
        stmt = select(
            func.coalesce(
                func.sum(
                    func.coalesce(AssistMessageModel.tokens_in, 0)
                    + func.coalesce(AssistMessageModel.tokens_out, 0)
                ),
                0,
            )
        ).where(
            and_(
                AssistMessageModel.user_id == user_id,
                AssistMessageModel.created_at >= since,
            )
        )
        result = (await self._session.execute(stmt)).scalar_one()
        return int(result)

    async def delete_user_conversations(self, user_id: str) -> int:
        # Messages are cascade-deleted via FK on assist_messages.conversation_id
        stmt = (
            delete(AssistConversationModel)
            .where(AssistConversationModel.user_id == user_id)
            .returning(AssistConversationModel.id)
        )
        result = await self._session.execute(stmt)
        rows = result.all()
        ids = [r[0] for r in rows]

        # Unlink the activity, do not delete it. The Studio project the agent
        # created still exists and the story edit still happened; clearing a
        # chat must not erase the record of what was done in it, or the audit
        # trail is one anybody can rewrite by pressing Clear.
        #
        # This is why conversation_id is not a foreign key: the reference is
        # allowed to dangle, and here it is deliberately cut.
        if ids:
            await self._session.execute(
                update(ActivityLogModel)
                .where(ActivityLogModel.conversation_id.in_(ids))
                .values(conversation_id=None, message_id=None)
            )
        return len(rows)

    async def usage_history_since(
        self, user_id: str, since: datetime
    ) -> list[DailyUsage]:
        day = func.date_trunc("day", AssistMessageModel.created_at).label("day")
        stmt = (
            select(
                day,
                func.coalesce(func.sum(AssistMessageModel.tokens_in), 0).label("tin"),
                func.coalesce(func.sum(AssistMessageModel.tokens_out), 0).label("tout"),
            )
            .where(
                and_(
                    AssistMessageModel.user_id == user_id,
                    AssistMessageModel.created_at >= since,
                )
            )
            .group_by(day)
            .order_by(day.asc())
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            DailyUsage(day=row.day.date(), tokens_in=int(row.tin), tokens_out=int(row.tout))
            for row in rows
        ]
