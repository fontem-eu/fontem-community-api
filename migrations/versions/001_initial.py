"""Initial schema

Revision ID: 001
Revises:
Create Date: 2026-04-04

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID

# revision identifiers, used by Alembic.
revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── users ─────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column("email", sa.Text, unique=True, nullable=False),
        sa.Column("name", sa.Text, nullable=False, server_default=""),
        sa.Column("avatar_url", sa.Text, nullable=True),
        sa.Column("trust_level", sa.Text, nullable=False, server_default="new_user"),
        sa.Column(
            "created_at",
            TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # ── user_roles ────────────────────────────────────────────────
    op.create_table(
        "user_roles",
        sa.Column("user_id", UUID(as_uuid=False), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("role", sa.Text, nullable=False),
        sa.PrimaryKeyConstraint("user_id", "role"),
    )

    # ── groups ────────────────────────────────────────────────────
    op.create_table(
        "groups",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column(
            "created_at",
            TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # ── group_members ─────────────────────────────────────────────
    op.create_table(
        "group_members",
        sa.Column(
            "group_id", UUID(as_uuid=False), sa.ForeignKey("groups.id"), nullable=False
        ),
        sa.Column(
            "user_id", UUID(as_uuid=False), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.PrimaryKeyConstraint("group_id", "user_id"),
    )

    # ── reports ───────────────────────────────────────────────────
    op.create_table(
        "reports",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column("title", sa.Text, nullable=False, server_default=""),
        sa.Column("abstract", sa.Text, nullable=True),
        sa.Column("visibility", sa.Text, nullable=False, server_default="private"),
        sa.Column(
            "created_by", UUID(as_uuid=False), sa.ForeignKey("users.id"), nullable=False
        ),
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
    )

    # ── report_access ─────────────────────────────────────────────
    op.create_table(
        "report_access",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "report_id",
            UUID(as_uuid=False),
            sa.ForeignKey("reports.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id", UUID(as_uuid=False), sa.ForeignKey("users.id"), nullable=True
        ),
        sa.Column(
            "group_id", UUID(as_uuid=False), sa.ForeignKey("groups.id"), nullable=True
        ),
        sa.Column("level", sa.Text, nullable=False, server_default="viewer"),
    )

    # ── sections ──────────────────────────────────────────────────
    op.create_table(
        "sections",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "report_id",
            UUID(as_uuid=False),
            sa.ForeignKey("reports.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column("content_json", JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "lock_holder", UUID(as_uuid=False), sa.ForeignKey("users.id"), nullable=True
        ),
        sa.Column("lock_expires", TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # ── section_versions ──────────────────────────────────────────
    op.create_table(
        "section_versions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "section_id",
            UUID(as_uuid=False),
            sa.ForeignKey("sections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("content_json", JSONB, nullable=False),
        sa.Column(
            "saved_by", UUID(as_uuid=False), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column(
            "saved_at",
            TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # ── issues ────────────────────────────────────────────────────
    op.create_table(
        "issues",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column("title", sa.Text, nullable=False, server_default=""),
        sa.Column("body_md", sa.Text, nullable=False, server_default=""),
        sa.Column("issue_type", sa.Text, nullable=False, server_default="other"),
        sa.Column("entity_type", sa.Text, nullable=False, server_default=""),
        sa.Column("entity_id", sa.Text, nullable=False, server_default=""),
        sa.Column("status", sa.Text, nullable=False, server_default="open"),
        sa.Column(
            "created_by", UUID(as_uuid=False), sa.ForeignKey("users.id"), nullable=False
        ),
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
    )

    # ── comments ──────────────────────────────────────────────────
    op.create_table(
        "comments",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column("parent_type", sa.Text, nullable=False, server_default=""),
        sa.Column("parent_id", UUID(as_uuid=False), nullable=False),
        sa.Column("body_md", sa.Text, nullable=False, server_default=""),
        sa.Column(
            "author_id", UUID(as_uuid=False), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column(
            "created_at",
            TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # ── issue_votes ───────────────────────────────────────────────
    op.create_table(
        "issue_votes",
        sa.Column("issue_id", UUID(as_uuid=False), nullable=False),
        sa.Column("user_id", UUID(as_uuid=False), nullable=False),
        sa.Column("direction", sa.Text, nullable=False),
        sa.PrimaryKeyConstraint("issue_id", "user_id"),
    )

    # ── flags ─────────────────────────────────────────────────────
    op.create_table(
        "flags",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column("target_type", sa.Text, nullable=False, server_default=""),
        sa.Column("target_id", UUID(as_uuid=False), nullable=False),
        sa.Column("reason", sa.Text, nullable=False, server_default="other"),
        sa.Column("details", sa.Text, nullable=False, server_default=""),
        sa.Column(
            "flagged_by", UUID(as_uuid=False), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column(
            "created_at",
            TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # ── sanctions ─────────────────────────────────────────────────
    op.create_table(
        "sanctions",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "user_id", UUID(as_uuid=False), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("type", sa.Text, nullable=False, server_default="warning"),
        sa.Column("reason", sa.Text, nullable=False, server_default=""),
        sa.Column("starts_at", TIMESTAMP(timezone=True), nullable=True),
        sa.Column("expires_at", TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "applied_by", UUID(as_uuid=False), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("lifted_at", TIMESTAMP(timezone=True), nullable=True),
    )

    # ── moderation_log ────────────────────────────────────────────
    op.create_table(
        "moderation_log",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("action", sa.Text, nullable=False),
        sa.Column("details", JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "actor_id", UUID(as_uuid=False), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column(
            "created_at",
            TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("moderation_log")
    op.drop_table("sanctions")
    op.drop_table("flags")
    op.drop_table("issue_votes")
    op.drop_table("comments")
    op.drop_table("issues")
    op.drop_table("section_versions")
    op.drop_table("sections")
    op.drop_table("report_access")
    op.drop_table("reports")
    op.drop_table("group_members")
    op.drop_table("groups")
    op.drop_table("user_roles")
    op.drop_table("users")
