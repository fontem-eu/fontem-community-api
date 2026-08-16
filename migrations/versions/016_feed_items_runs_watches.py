"""briefings: materialised feed items, run bookkeeping, and watches

Revision ID: 016
Revises: 015
Create Date: 2026-08-15

The spine of the Briefings feature. Three tables:

``feed_items`` is the materialised output of the catalogue's published
queries. It exists because Atom readers poll every 15-60 minutes and no
arbitrary graph query can be run per poll — the runner executes on its own
cadence and the feed endpoint only ever reads rows.

It also, quietly, settles the watermark problem this design was stuck on.
None of the upstream stores record when WE learned a fact — not the stats
store, not Neo4j; every :Contract property describes the notice, none the
ingestion. ``first_seen_at`` is that timestamp by construction: the runner
sets it the first time an item_id appears, and the unique constraint on
(query_id, item_id) means it never moves afterwards. "What is new since I
last looked" becomes a question about our own table.

``feed_runs`` records what each run did, including whether a partition came
back truncated. Silent truncation is the failure mode this whole design has
been avoiding, so a run that dropped rows has to say so.

``watches`` is a subscription to a BRIEFING (a query group), scoped to the
watcher's regions and expressed as a VOLUME rather than a threshold — "about
ten a week". Measured on prod, the 95th percentile of contract value is EUR
5.7M in Coimbra and EUR 10.8M across the EU, so one threshold starves a small
region and floods a large one at the same time. Ranking happens at read time
against these rows, in whichever regions the watcher picked.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, TIMESTAMP, UUID

revision: str = "016"
down_revision: Union[str, None] = "015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "feed_items",
        sa.Column("id", UUID(as_uuid=False), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("query_id", UUID(as_uuid=False),
                  sa.ForeignKey("named_queries.id", ondelete="CASCADE"), nullable=False),
        # The query's own stable id for the thing — the Atom <id>.
        sa.Column("item_id", sa.Text(), nullable=False),
        sa.Column("item_time", TIMESTAMP(timezone=True), nullable=False),
        sa.Column("nuts", ARRAY(sa.Text()), nullable=False, server_default="{}"),
        # The magnitude a feed ranks by. Nullable because a domain with no
        # natural magnitude (a legal act has no size) ranks by time alone.
        sa.Column("rank_value", sa.Numeric(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("link", sa.Text(), nullable=False, server_default=""),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        # When WE first saw it. The only ingestion timestamp in the system.
        sa.Column("first_seen_at", TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.UniqueConstraint("query_id", "item_id", name="feed_items_query_item_unique"),
    )
    op.create_index("ix_feed_items_query_time", "feed_items", ["query_id", "item_time"])
    op.create_index("ix_feed_items_first_seen", "feed_items", ["first_seen_at"])
    op.create_index("ix_feed_items_nuts", "feed_items", ["nuts"], postgresql_using="gin")

    op.create_table(
        "feed_runs",
        sa.Column("id", UUID(as_uuid=False), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("query_id", UUID(as_uuid=False),
                  sa.ForeignKey("named_queries.id", ondelete="CASCADE"), nullable=False),
        sa.Column("started_at", TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.Column("finished_at", TIMESTAMP(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="running"),
        sa.Column("partitions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("items_seen", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("items_new", sa.Integer(), nullable=False, server_default="0"),
        # A partition that came back at the proxy's row cap dropped rows.
        # Counted so truncation is never silent.
        sa.Column("truncated_partitions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.CheckConstraint("status IN ('running','ok','error')", name="feed_runs_status_check"),
    )
    op.create_index("ix_feed_runs_query_started", "feed_runs", ["query_id", "started_at"])

    op.create_table(
        "watches",
        sa.Column("id", UUID(as_uuid=False), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID(as_uuid=False),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("group_id", UUID(as_uuid=False),
                  sa.ForeignKey("query_groups.id", ondelete="CASCADE"), nullable=False),
        # The watcher's regions. ['EU'] means everywhere.
        sa.Column("nuts", ARRAY(sa.Text()), nullable=False, server_default="{EU}"),
        # A volume, not a threshold: how many items a week this watcher wants.
        sa.Column("volume_per_week", sa.Integer(), nullable=False, server_default="10"),
        # Atom readers cannot authenticate, so the URL carries the secret.
        # Revocable by deleting the row; it names the watch, not the person.
        sa.Column("token", sa.Text(), nullable=False, unique=True),
        sa.Column("created_at", TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.Column("updated_at", TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.Column("last_polled_at", TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint("volume_per_week BETWEEN 1 AND 200", name="watches_volume_check"),
        sa.UniqueConstraint("user_id", "group_id", name="watches_user_group_unique"),
    )
    op.create_index("ix_watches_user", "watches", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_watches_user", table_name="watches")
    op.drop_table("watches")
    op.drop_index("ix_feed_runs_query_started", table_name="feed_runs")
    op.drop_table("feed_runs")
    for idx in ("ix_feed_items_nuts", "ix_feed_items_first_seen", "ix_feed_items_query_time"):
        op.drop_index(idx, table_name="feed_items")
    op.drop_table("feed_items")
