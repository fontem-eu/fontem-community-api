"""Refresh-token families for session rotation + reuse detection.

A *family* is one continuous chain of refresh tokens that share a
single login. The chain rotates on every refresh — only the latest
``current_token_hash`` ever validates. Any reuse of an older token
(found because the hash no longer matches the latest) is treated as
compromise: the family gets ``revoked_at`` set and no further refresh
succeeds.

Drops the 30-day access JWT TTL down to 15 min in the application
code at the same time; the refresh-token family carries the long-
lived session state instead.

Revision ID: 006
Revises: 005
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "refresh_token_families",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("current_token_hash", sa.Text, nullable=False),
        sa.Column(
            "rotated_at",
            sa.dialects.postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "expires_at",
            sa.dialects.postgresql.TIMESTAMP(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "revoked_at",
            sa.dialects.postgresql.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.Column("revoked_reason", sa.Text, nullable=True),
        sa.Column("created_user_agent_hash", sa.Text, nullable=True),
        sa.Column("created_ip_hash", sa.Text, nullable=True),
    )
    op.create_index(
        "ix_refresh_token_families_user_id",
        "refresh_token_families",
        ["user_id"],
    )
    op.create_index(
        "ix_refresh_token_families_current_token_hash",
        "refresh_token_families",
        ["current_token_hash"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_refresh_token_families_current_token_hash",
        table_name="refresh_token_families",
    )
    op.drop_index(
        "ix_refresh_token_families_user_id",
        table_name="refresh_token_families",
    )
    op.drop_table("refresh_token_families")
