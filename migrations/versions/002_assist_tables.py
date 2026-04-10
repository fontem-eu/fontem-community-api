"""Assistant-owned tables: assist_conversations, assist_messages.

Revision ID: 002
Revises: 001
Create Date: 2026-04-11

Introduces two new tables owned by the assistant module. The older
``conversations`` table stays in place for one release cycle; the
new code writes to the new tables and reads nothing from the old one.
A follow-up migration will drop ``conversations`` once we have
confirmed the new path is stable in production.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID


revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "assist_conversations",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=False), nullable=False),
        sa.Column("conversation_key", sa.Text, nullable=False),
        sa.Column(
            "created_at",
            TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "user_id", "conversation_key",
            name="uq_assist_conv_user_key",
        ),
    )

    op.create_table(
        "assist_messages",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "conversation_id",
            UUID(as_uuid=False),
            sa.ForeignKey("assist_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", UUID(as_uuid=False), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column(
            "extras", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("tokens_in", sa.Integer, nullable=True),
        sa.Column("tokens_out", sa.Integer, nullable=True),
        sa.Column("model", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "role IN ('user', 'assistant')", name="ck_assist_msg_role",
        ),
    )

    op.create_index(
        "ix_assist_msg_user_created",
        "assist_messages",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_assist_msg_conv_created",
        "assist_messages",
        ["conversation_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_assist_msg_conv_created", table_name="assist_messages")
    op.drop_index("ix_assist_msg_user_created", table_name="assist_messages")
    op.drop_table("assist_messages")
    op.drop_table("assist_conversations")
