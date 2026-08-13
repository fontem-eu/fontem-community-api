"""feed-query catalogue: named_queries, query_groups, query_group_members

Revision ID: 012
Revises: 011
Create Date: 2026-08-13

The catalogue behind the notable-events feed. A named query is an
editorially-curated query against one of the platform's stores; a query group
is an ordered set of them, and what the public picker shows.

Three tables rather than two-plus-a-column because membership is genuinely
many-to-many: a query about energy-sector lobbying belongs in both "Corporate
influence" and "Energy" without being duplicated. sort_order lives on the
membership row so the same query can sit at a different position in each group.

Deliberately NOT an extension of data_queries. That table is project-scoped
and owner-private (a Data Studio workspace artifact); this is platform content
with a stable slug, a publication state, and a recorded contract verdict.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID

revision: str = "012"
down_revision: Union[str, None] = "011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "named_queries",
        sa.Column("id", UUID(as_uuid=False), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        # The stable public reference: subscriptions and feed URLs point at
        # the slug, so the query text can be rewritten without breaking them.
        sa.Column("slug", sa.Text(), nullable=False, unique=True),
        sa.Column("name", sa.Text(), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("lang", sa.Text(), nullable=False, server_default="sql"),
        sa.Column("query", sa.Text(), nullable=False, server_default=""),
        sa.Column("params", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("status", sa.Text(), nullable=False, server_default="draft"),
        # check id -> the admin's written reason. A waiver always costs a
        # sentence; nothing can be waved through in silence.
        sa.Column("waivers", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("contract_ok", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("contract_report", JSONB(), nullable=True),
        sa.Column("validated_at", TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_by", UUID(as_uuid=False),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.Column("updated_at", TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.CheckConstraint("status IN ('draft','published','retired')",
                           name="named_queries_status_check"),
        sa.CheckConstraint("lang IN ('sql','cypher','sparql')",
                           name="named_queries_lang_check"),
    )
    # The public catalogue reads "published only", which is the hot path.
    op.create_index("ix_named_queries_status", "named_queries", ["status"])

    op.create_table(
        "query_groups",
        sa.Column("id", UUID(as_uuid=False), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("slug", sa.Text(), nullable=False, unique=True),
        sa.Column("name", sa.Text(), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("visibility", sa.Text(), nullable=False, server_default="public"),
        sa.Column("created_at", TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.Column("updated_at", TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.CheckConstraint("visibility IN ('public','admin')",
                           name="query_groups_visibility_check"),
    )

    op.create_table(
        "query_group_members",
        sa.Column("group_id", UUID(as_uuid=False),
                  sa.ForeignKey("query_groups.id", ondelete="CASCADE"),
                  primary_key=True, nullable=False),
        sa.Column("query_id", UUID(as_uuid=False),
                  sa.ForeignKey("named_queries.id", ondelete="CASCADE"),
                  primary_key=True, nullable=False),
        # On the membership row, not on the query: a query in two groups has
        # a position in each.
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
    )
    # "which groups is this query in?" is the admin UI's per-query panel.
    op.create_index("ix_query_group_members_query", "query_group_members", ["query_id"])


def downgrade() -> None:
    op.drop_index("ix_query_group_members_query", table_name="query_group_members")
    op.drop_table("query_group_members")
    op.drop_table("query_groups")
    op.drop_index("ix_named_queries_status", table_name="named_queries")
    op.drop_table("named_queries")
