"""user_llm_credentials: per-user provider keys, encrypted at rest

Revision ID: 009
Revises: 008
Create Date: 2026-08-04

Users bring their own LLM provider instead of spending a platform-wide
key. `secret_enc` holds AEAD ciphertext whose master key arrives through
the environment from Vault, so a database dump on its own is inert.

ON DELETE CASCADE on user_id is deliberate: deleting an account must take
the third-party credential with it, and leaving that to application code
means the one path that forgets leaves a live key behind.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID

revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_llm_credentials",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "user_id", UUID(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("secret_enc", sa.Text(), nullable=False),
        sa.Column("fingerprint", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("last_used_at", TIMESTAMP(timezone=True), nullable=True),
        sa.UniqueConstraint("user_id", "provider", name="uq_user_provider"),
    )
    op.create_index(
        "ix_user_llm_credentials_user_id", "user_llm_credentials", ["user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_user_llm_credentials_user_id", table_name="user_llm_credentials")
    op.drop_table("user_llm_credentials")
