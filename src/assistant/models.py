"""SQLAlchemy models for the assistant module.

These live under ``src/assistant/`` to keep the module self-contained.
They share the global ``Base`` so Alembic autogenerate sees them, but
import nothing else from ``src.infra.postgres.models`` or any domain
layer. If the module is ever extracted to its own service, only this
file and the Alembic migration need to move.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infra.postgres.models import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_uuid() -> str:
    return str(uuid4())


class AssistConversationModel(Base):
    """A single conversation stream, keyed by an opaque caller key.

    The assistant doesn't know what the key means — it might be
    ``report:<uuid>``, ``standalone:<uuid>``, etc. That's the caller's
    call, and the isolation is per user.
    """

    __tablename__ = "assist_conversations"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "conversation_key",
            name="uq_assist_conv_user_key",
        ),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_new_uuid
    )
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), nullable=False,
    )
    conversation_key: Mapped[str] = mapped_column(Text, nullable=False)
    #: What the switcher shows. Nullable: a conversation has no title until
    #: it has a first message to take one from, and an "Untitled" row is
    #: worse than an absent title the UI fills in from the opening question.
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: A rolling note of what fell out of the continuity window. Absent until
    #: the conversation first overflows, which on a large-context model is
    #: never.
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: The last message already folded into `summary`, so the next overflow
    #: summarises only what has fallen off since.
    summary_through: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=_utcnow,
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False,
        default=_utcnow, onupdate=_utcnow,
    )


class AssistMessageModel(Base):
    """A single turn in a conversation.

    ``user_id`` is denormalized from the parent conversation so that
    per-user rate queries can hit a compound index without a join.
    """

    __tablename__ = "assist_messages"
    __table_args__ = (
        # 'tool' joined the vocabulary when tool calls became conversation
        # rows. The constraint is the reason that has to be a migration and
        # not just a code change: it lives only in the database, so every
        # in-memory test passed while production rejected the row.
        CheckConstraint(
            "role IN ('user', 'assistant', 'tool')",
            name="ck_assist_msg_role",
        ),
        Index("ix_assist_msg_user_created", "user_id", "created_at"),
        Index("ix_assist_msg_conv_created", "conversation_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_new_uuid,
    )
    conversation_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("assist_conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), nullable=False,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    extras: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    tokens_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=_utcnow,
    )
