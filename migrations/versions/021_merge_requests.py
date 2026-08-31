"""Merge requests: a proposal to move an article's published text.

The shape the editors chose: one draft branch per editor (already
enforced by ``doc_branches``), and a formal review in front of anything
that reaches main. Articles here take days to write, so the ceremony is
worth what it costs — this is a merge request, not a live cursor.

Three decisions are encoded here rather than left to the caller:

* ``self_merged`` — an author may merge their own request, because solo
  authorship is the common case and forbidding it would only teach people
  to route around review. But it is recorded, so "this went in without a
  second reader" stays visible afterwards.
* Nothing expires. A draft that sits behind main for months is somebody's
  unfinished work, not garbage to collect.
* Open requests are for people with edit access. A proposal nobody has
  reviewed is not yet a claim the platform is making.

Revision ID: 021
Revises: 020
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "021"
down_revision: Union[str, None] = "020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UUID = sa.dialects.postgresql.UUID(as_uuid=False)
_TS = sa.dialects.postgresql.TIMESTAMP(timezone=True)


def _has(bind, table: str) -> bool:
    return sa.inspect(bind).has_table(table)


def upgrade() -> None:
    bind = op.get_bind()
    if not _has(bind, "doc_revisions"):
        # Fresh build: create_all makes these from the models.
        return

    # 022 renames these to reviews/review_comments. When the chain is
    # re-run from a stamp older than that — which the idempotency test
    # does, and a redeploy can — the tables are already there under their
    # later names, and recreating them collides on the index names that
    # a rename carries along.
    if _has(bind, "reviews"):
        return

    if not _has(bind, "merge_requests"):
        op.create_table(
            "merge_requests",
            sa.Column("id", _UUID, primary_key=True),
            sa.Column("report_id", _UUID,
                      sa.ForeignKey("reports.id", ondelete="CASCADE"),
                      nullable=False),
            sa.Column("author_id", _UUID, sa.ForeignKey("users.id"),
                      nullable=False),
            sa.Column("title", sa.Text, nullable=False, server_default=""),
            sa.Column("body", sa.Text, nullable=False, server_default=""),
            # What is proposed, and what it was written against.
            sa.Column("source_head", _UUID,
                      sa.ForeignKey("doc_revisions.id"), nullable=False),
            sa.Column("target_base", _UUID,
                      sa.ForeignKey("doc_revisions.id"), nullable=False),
            sa.Column("state", sa.Text, nullable=False, server_default="open"),
            sa.Column("created_at", _TS, nullable=False,
                      server_default=sa.func.now()),
            sa.Column("updated_at", _TS, nullable=False,
                      server_default=sa.func.now()),
            sa.Column("merged_at", _TS, nullable=True),
            sa.Column("merged_by", _UUID, sa.ForeignKey("users.id"),
                      nullable=True),
            sa.Column("merged_revision_id", _UUID,
                      sa.ForeignKey("doc_revisions.id"), nullable=True),
            sa.Column("self_merged", sa.Boolean, nullable=False,
                      server_default=sa.text("false")),
            sa.CheckConstraint(
                "state IN ('open', 'merged', 'closed')",
                name="ck_merge_requests_state"),
        )
        op.create_index("ix_merge_requests_report_state", "merge_requests",
                        ["report_id", "state"])
        # One open request per editor per article: the draft branch is
        # singular, so a second open request for it would be a duplicate
        # of the first rather than a different proposal.
        op.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_merge_requests_open "
            "ON merge_requests (report_id, author_id) WHERE state = 'open'"
        )

    if not _has(bind, "mr_comments") and not _has(bind, "review_comments"):
        op.create_table(
            "mr_comments",
            sa.Column("id", _UUID, primary_key=True),
            sa.Column("mr_id", _UUID,
                      sa.ForeignKey("merge_requests.id", ondelete="CASCADE"),
                      nullable=False),
            sa.Column("author_id", _UUID, sa.ForeignKey("users.id"),
                      nullable=False),
            # Which block the comment is about. A block key rather than a
            # line number, so a comment survives edits elsewhere in the
            # document.
            sa.Column("anchor", sa.Text, nullable=True),
            sa.Column("body", sa.Text, nullable=False),
            sa.Column("resolved", sa.Boolean, nullable=False,
                      server_default=sa.text("false")),
            sa.Column("created_at", _TS, nullable=False,
                      server_default=sa.func.now()),
        )
        op.create_index("ix_mr_comments_mr", "mr_comments", ["mr_id"])


def downgrade() -> None:
    bind = op.get_bind()
    if _has(bind, "mr_comments"):
        op.drop_table("mr_comments")
    if _has(bind, "merge_requests"):
        op.drop_table("merge_requests")
