"""activity_log: who caused this, and on whose behalf

Revision ID: 012
Revises: 011
Create Date: 2026-08-13

The activity log answered "what happened" and, implicitly, "a user did it".
Once an agent can write to the platform on a user's behalf, that implication
is wrong: an entry saying the user created a Studio project is false when the
assistant created it, and there was no way to tell the two apart.

Four columns, all nullable so the 29 existing rows stay valid:

  actor_kind       'user' or 'agent'. Defaults to 'user' because everything
                   written before this migration was.
  conversation_id  the assistant conversation, when an agent caused it.
  message_id       the specific tool call inside that conversation.
  request_id       correlates every entry written while serving one request,
                   including several writes from one agent turn.

conversation_id and message_id are deliberately NOT foreign keys. The
reference is allowed to dangle: deleting a conversation unlinks the activity
rather than deleting it, because the story edit still happened and an audit
trail you can erase by clearing your chat is not one. An FK with ON DELETE
SET NULL would express that, but it also forces insert ordering inside a
turn — the audit row is written while the tool runs, the conversation row
when its event reaches the service — and a deferred constraint to work
around that buys nothing a nullable column does not already give.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "012"
down_revision: Union[str, None] = "011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "activity_log",
        sa.Column("actor_kind", sa.Text(), nullable=False,
                  server_default=sa.text("'user'")),
    )
    op.add_column(
        "activity_log",
        sa.Column("conversation_id", UUID(as_uuid=False), nullable=True),
    )
    op.add_column(
        "activity_log",
        sa.Column("message_id", UUID(as_uuid=False), nullable=True),
    )
    op.add_column(
        "activity_log",
        sa.Column("request_id", sa.Text(), nullable=True),
    )
    # "Show me everything the assistant did in this conversation" is the
    # question this table now exists to answer quickly.
    op.create_index(
        "ix_activity_log_conversation",
        "activity_log", ["conversation_id"],
        postgresql_where=sa.text("conversation_id IS NOT NULL"),
    )
    op.create_index("ix_activity_log_request", "activity_log", ["request_id"])


def downgrade() -> None:
    op.drop_index("ix_activity_log_request", table_name="activity_log")
    op.drop_index("ix_activity_log_conversation", table_name="activity_log")
    op.drop_column("activity_log", "request_id")
    op.drop_column("activity_log", "message_id")
    op.drop_column("activity_log", "conversation_id")
    op.drop_column("activity_log", "actor_kind")
