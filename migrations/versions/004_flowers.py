"""Flowers given — Medium-style clap on stories.

Revision ID: 004
Revises: 003
Create Date: 2026-06-08

Each row aggregates one user's flower-giving to one story. The PK is
(user_id, report_id), so atomic upserts via ON CONFLICT DO UPDATE
turn a click into a single round-trip with no read-modify-write race.

App-level cap (FlowerService.MAX_FLOWERS_PER_USER = 50) is mirrored
by the CHECK constraint below so an out-of-band write can't push the
count past the cap. The lower bound 0 keeps the column honest if we
ever decide to allow take-backs.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "flowers_given",
        sa.Column(
            "user_id",
            UUID(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "report_id",
            UUID(as_uuid=False),
            sa.ForeignKey("reports.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("count", sa.Integer, nullable=False, server_default=sa.text("1")),
        sa.Column(
            "created_at",
            TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "count >= 0 AND count <= 50",
            name="flowers_given_count_check",
        ),
    )
    # Aggregate read: SUM(count) WHERE report_id = ? — a single-column
    # index on report_id keeps that scan cheap even when one story
    # has been clapped thousands of times.
    op.create_index(
        "ix_flowers_given_report_id",
        "flowers_given",
        ["report_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_flowers_given_report_id", table_name="flowers_given")
    op.drop_table("flowers_given")
