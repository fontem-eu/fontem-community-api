"""Tags: story_tags + user_followed_tags + seed existing public stories.

Revision ID: 003
Revises: 002
Create Date: 2026-05-08

Tags are implicitly defined — there is no `tags` table. The set of
existing tags is `SELECT DISTINCT tag FROM story_tags`. Cardinality
is small (low-thousands) and the per-story limit (3) keeps growth
linear with story count, so the cost of distinct-scan is fine for
the foreseeable future.

App-level limits enforced in `TagService`:
  - ≤3 tags per story
  - ≤50 followed tags per user
DB-level enforcement (a trigger) is a future hardening if writes
ever race; for the current write volume the app-level guard is
enough.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── story_tags ────────────────────────────────────────────────
    # Tags are stored as their slug form (lowercase, [a-z0-9-]). The
    # service layer normalises before insert; the CHECK constraint
    # below stops out-of-band writes from sneaking in a non-slug.
    op.create_table(
        "story_tags",
        sa.Column(
            "report_id",
            UUID(as_uuid=False),
            sa.ForeignKey("reports.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("tag", sa.Text, primary_key=True),
        sa.Column(
            "created_at",
            TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "tag ~ '^[a-z0-9]+(-[a-z0-9]+)*$' AND char_length(tag) <= 40",
            name="story_tags_slug_check",
        ),
    )
    op.create_index("ix_story_tags_tag", "story_tags", ["tag"])

    # ── user_followed_tags ────────────────────────────────────────
    op.create_table(
        "user_followed_tags",
        sa.Column(
            "user_id",
            UUID(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("tag", sa.Text, primary_key=True),
        sa.Column(
            "followed_at",
            TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "tag ~ '^[a-z0-9]+(-[a-z0-9]+)*$' AND char_length(tag) <= 40",
            name="user_followed_tags_slug_check",
        ),
    )
    op.create_index("ix_user_followed_tags_tag", "user_followed_tags", ["tag"])

    # ── seed: tag every existing public story with public-expenditure ─
    # Skim of the existing reports turned up that they're all
    # public-spending investigations; one shared tag is enough to
    # bootstrap browse-by-tag without falsely splitting them.
    # Idempotent under re-run via ON CONFLICT DO NOTHING.
    op.execute(
        """
        INSERT INTO story_tags (report_id, tag, created_at)
        SELECT id, 'public-expenditure', now()
        FROM reports
        WHERE visibility IN ('public_open', 'public_auth')
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_index("ix_user_followed_tags_tag", table_name="user_followed_tags")
    op.drop_table("user_followed_tags")
    op.drop_index("ix_story_tags_tag", table_name="story_tags")
    op.drop_table("story_tags")
