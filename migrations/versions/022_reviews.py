"""Reviews as a first-class thing, in two kinds.

A *change review* is what merge_requests was: a draft proposed as the
article's published text, read as a diff and merged when it is right.

A *full article review* is the other half the editors asked for: no diff
and no merge, just inline comments on one version of a finished article —
a self-review before publishing, or somebody else's read.

They are one table because they are one workflow with one difference:
whether there is something to merge at the end. Two tables would have
meant two comment tables, two reviewer tables, two "my reviews" queries,
and a rename the first time someone wanted to comment on both.

Renames rather than new tables: 021 shipped merge_requests to testing but
nowhere else, and carrying a second vocabulary for the same rows would
cost more than moving them. Every step is guarded, because this must work
on a database that already has 021's tables and on one applying the whole
sequence at once.

Revision ID: 022
Revises: 021
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "022"
down_revision: Union[str, None] = "021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UUID = sa.dialects.postgresql.UUID(as_uuid=False)
_TS = sa.dialects.postgresql.TIMESTAMP(timezone=True)


def _has(bind, table: str) -> bool:
    return sa.inspect(bind).has_table(table)


def _columns(bind, table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has(bind, "reports"):
        return

    if _has(bind, "merge_requests") and not _has(bind, "reviews"):
        op.rename_table("merge_requests", "reviews")
    if _has(bind, "mr_comments") and not _has(bind, "review_comments"):
        op.rename_table("mr_comments", "review_comments")
        op.alter_column("review_comments", "mr_id", new_column_name="review_id")

    if not _has(bind, "reviews"):
        # A database that never saw 021.
        _create_reviews(bind)
    else:
        _extend_reviews(bind)

    if not _has(bind, "review_comments"):
        _create_comments()

    if not _has(bind, "review_reviewers"):
        op.create_table(
            "review_reviewers",
            sa.Column("review_id", _UUID,
                      sa.ForeignKey("reviews.id", ondelete="CASCADE"),
                      nullable=False),
            sa.Column("user_id", _UUID,
                      sa.ForeignKey("users.id", ondelete="CASCADE"),
                      nullable=False),
            sa.Column("invited_by", _UUID, sa.ForeignKey("users.id"),
                      nullable=False),
            sa.Column("invited_at", _TS, nullable=False,
                      server_default=sa.func.now()),
            sa.PrimaryKeyConstraint("review_id", "user_id"),
        )
        op.create_index("ix_review_reviewers_user", "review_reviewers",
                        ["user_id"])


def _create_reviews(bind) -> None:  # pylint: disable=unused-argument
    op.create_table(
        "reviews",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("report_id", _UUID,
                  sa.ForeignKey("reports.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("kind", sa.Text, nullable=False, server_default="change"),
        sa.Column("author_id", _UUID, sa.ForeignKey("users.id"),
                  nullable=False),
        sa.Column("title", sa.Text, nullable=False, server_default=""),
        sa.Column("body", sa.Text, nullable=False, server_default=""),
        sa.Column("source_head", _UUID, sa.ForeignKey("doc_revisions.id"),
                  nullable=False),
        sa.Column("target_base", _UUID, sa.ForeignKey("doc_revisions.id"),
                  nullable=True),
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
        sa.CheckConstraint("kind IN ('change', 'article')",
                           name="ck_reviews_kind"),
        sa.CheckConstraint(
            "state IN ('open', 'merged', 'closed', 'completed')",
            name="ck_reviews_state"),
    )
    _reviews_indexes()


def _extend_reviews(bind) -> None:
    """Bring 021's merge_requests up to the review shape."""
    columns = _columns(bind, "reviews")
    if "kind" not in columns:
        op.add_column("reviews", sa.Column(
            "kind", sa.Text, nullable=False, server_default="change"))
    # An article review has nothing to merge into, so it has no base.
    op.alter_column("reviews", "target_base", nullable=True)

    for name in ("ck_merge_requests_state", "ck_reviews_state"):
        op.execute(f"ALTER TABLE reviews DROP CONSTRAINT IF EXISTS {name}")
    op.create_check_constraint(
        "ck_reviews_state", "reviews",
        "state IN ('open', 'merged', 'closed', 'completed')")
    op.execute("ALTER TABLE reviews DROP CONSTRAINT IF EXISTS ck_reviews_kind")
    op.create_check_constraint(
        "ck_reviews_kind", "reviews", "kind IN ('change', 'article')")

    op.execute("DROP INDEX IF EXISTS uq_merge_requests_open")
    op.execute("DROP INDEX IF EXISTS ix_merge_requests_report_state")
    _reviews_indexes()


def _reviews_indexes() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_reviews_report_state "
        "ON reviews (report_id, state)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_reviews_author ON reviews (author_id)")
    # One open CHANGE review per editor per article: the draft branch is
    # singular, so a second would be the same proposal twice. Article
    # reviews carry no such limit — a piece can be read by several people
    # at once, and each read is its own conversation.
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_reviews_open_change "
        "ON reviews (report_id, author_id) "
        "WHERE state = 'open' AND kind = 'change'"
    )


def _create_comments() -> None:
    op.create_table(
        "review_comments",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("review_id", _UUID,
                  sa.ForeignKey("reviews.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("author_id", _UUID, sa.ForeignKey("users.id"),
                  nullable=False),
        sa.Column("anchor", sa.Text, nullable=True),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("resolved", sa.Boolean, nullable=False,
                  server_default=sa.text("false")),
        sa.Column("created_at", _TS, nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index("ix_review_comments_review", "review_comments",
                    ["review_id"])


def downgrade() -> None:
    bind = op.get_bind()
    if _has(bind, "review_reviewers"):
        op.drop_table("review_reviewers")
    if _has(bind, "review_comments"):
        op.rename_table("review_comments", "mr_comments")
        op.alter_column("mr_comments", "review_id", new_column_name="mr_id")
    if _has(bind, "reviews"):
        op.rename_table("reviews", "merge_requests")
