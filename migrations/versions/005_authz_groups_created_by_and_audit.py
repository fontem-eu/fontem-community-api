"""Authorization service: groups.created_by + authz_audit table.

Closes the IDOR on POST/DELETE /groups/{id}/members where any
authenticated user could add/remove anyone from any group: the
``created_by`` column gives the AuthorizationService a stable
anchor to gate membership management against. Pre-existing rows
without a creator are deliberately left NULL and treated as
admin-only by the policy (since we don't know who created them).

Also creates ``authz_audit`` — the central record of every authz
decision the AuthorizationService makes. Indexed by user_id +
timestamp for the common forensic query "what did user X do?";
secondary index on (action, timestamp) supports "all moderation
activity over the last week" style aggregates.

Revision ID: 005
Revises: 004
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── groups.created_by ──────────────────────────────────
    # FK to users.id with ON DELETE SET NULL so deleting a user doesn't
    # cascade-delete the groups they created; the groups become legacy
    # rows that only admin can act on, matching the policy's "no owner
    # = admin only" gate. Nullable for backfill compatibility.
    op.add_column(
        "groups",
        sa.Column(
            "created_by",
            sa.dialects.postgresql.UUID(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    # ── authz_audit ────────────────────────────────────────
    op.create_table(
        "authz_audit",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=False),
            primary_key=True,
        ),
        sa.Column(
            "timestamp",
            sa.dialects.postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        # user_id intentionally NOT a FK — we want audit rows to
        # survive user deletion (the whole point of an audit trail
        # is that it outlives the actor).
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=False),
            nullable=True,
        ),
        # Plain text rather than an enum: adding a new Action shouldn't
        # require an enum alter on a hot table.
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("resource_kind", sa.Text(), nullable=True),
        sa.Column(
            "resource_id",
            sa.dialects.postgresql.UUID(as_uuid=False),
            nullable=True,
        ),
        sa.Column("allowed", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
    )
    op.create_index(
        "ix_authz_audit_user_timestamp",
        "authz_audit",
        ["user_id", "timestamp"],
    )
    op.create_index(
        "ix_authz_audit_action_timestamp",
        "authz_audit",
        ["action", "timestamp"],
    )


def downgrade() -> None:
    op.drop_index("ix_authz_audit_action_timestamp", table_name="authz_audit")
    op.drop_index("ix_authz_audit_user_timestamp", table_name="authz_audit")
    op.drop_table("authz_audit")
    op.drop_column("groups", "created_by")
