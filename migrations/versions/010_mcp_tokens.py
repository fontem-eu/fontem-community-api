"""mcp_tokens: personal access tokens for external MCP clients

Revision ID: 010
Revises: 009
Create Date: 2026-08-04

Only the SHA-256 hash is stored, so a database dump cannot be replayed.
Unique on token_hash because presenting a token is a single lookup, and
a collision would mean two users sharing an identity.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID

revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mcp_tokens",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=False),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("label", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("last_used_at", TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index("ix_mcp_tokens_user_id", "mcp_tokens", ["user_id"])
    op.create_index("ix_mcp_tokens_hash", "mcp_tokens", ["token_hash"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_mcp_tokens_hash", table_name="mcp_tokens")
    op.drop_index("ix_mcp_tokens_user_id", table_name="mcp_tokens")
    op.drop_table("mcp_tokens")
