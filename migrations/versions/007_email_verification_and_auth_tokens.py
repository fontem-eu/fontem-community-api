"""Email verification + password reset: users.email_verified_at + auth_tokens.

Adds the ``email_verified_at`` column (NULL = unverified) and the
``auth_tokens`` table that backs both the email-verification and
password-reset flows (single-use, hashed, expiring).

**Grandfathering**: every user that exists at migration time is
backfilled to ``email_verified_at = now()``. The "Required"
verification gate (2026-06-13 decision) only applies to accounts
created *after* this ships — we don't want to lock out users who
were already trusted.

NOTE for prod/staging: ``create_all`` does NOT ALTER an existing
``users`` table, so the column add + backfill must be run manually
against each environment's Postgres (see AUTHORIZATION.md's
"create_all gotcha" note). This migration is the canonical
dev/test reproduction.

Revision ID: 007
Revises: 006
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "email_verified_at",
            sa.dialects.postgresql.TIMESTAMP(timezone=True),
            nullable=True,
        ),
    )
    # Grandfather every pre-existing account.
    op.execute("UPDATE users SET email_verified_at = now() WHERE email_verified_at IS NULL")

    op.create_table(
        "auth_tokens",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.Text, nullable=False),
        sa.Column("purpose", sa.Text, nullable=False),
        sa.Column(
            "expires_at",
            sa.dialects.postgresql.TIMESTAMP(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "consumed_at",
            sa.dialects.postgresql.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.dialects.postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_auth_tokens_token_hash", "auth_tokens", ["token_hash"])
    op.create_index(
        "ix_auth_tokens_user_id_purpose", "auth_tokens", ["user_id", "purpose"],
    )


def downgrade() -> None:
    op.drop_index("ix_auth_tokens_user_id_purpose", table_name="auth_tokens")
    op.drop_index("ix_auth_tokens_token_hash", table_name="auth_tokens")
    op.drop_table("auth_tokens")
    op.drop_column("users", "email_verified_at")
