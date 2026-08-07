"""assistant_model_prefs: which built-in model a user chose

Revision ID: 011
Revises: 010
Create Date: 2026-08-07

Deliberately its own table rather than a column on user_llm_credentials.
That table holds secrets — secret_enc is NOT NULL — and a model choice is
not a secret. More importantly, "using the built-in model" is modelled as
having no credential row at all, so storing the built-in's settings on a
credential row would contradict the thing it means.

One row per user, so it is an upsert rather than a history.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID

revision: str = "011"
down_revision: Union[str, None] = "010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "assistant_model_prefs",
        sa.Column("user_id", UUID(as_uuid=False),
                  sa.ForeignKey("users.id", ondelete="CASCADE"),
                  primary_key=True, nullable=False),
        # The curated id from src/assistant/local_models.py, never a
        # filename — the weights behind it change without the preference
        # meaning anything different.
        sa.Column("model_id", sa.Text(), nullable=False),
        sa.Column("updated_at", TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
    )


def downgrade() -> None:
    op.drop_table("assistant_model_prefs")
