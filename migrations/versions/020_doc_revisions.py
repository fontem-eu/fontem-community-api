"""Revision history for article documents, and the pointers into it.

An article's text becomes a chain of immutable snapshots. ``doc_branches``
says where a chain currently points: NULL owner is main — the published
text — and any other owner is that editor's draft.

Snapshots, not stored deltas. Reading the current document is then one row
rather than a fold over the whole history, and one bad delta cannot poison
everything written after it. Git's object store works the same way; delta
compression is a later, invisible packing step. Revisions here average
about 4 kB, so the space this gives up is not worth the fragility it buys.

The existing history is carried over rather than dropped: every
``section_versions`` row becomes a revision, oldest first, parent-linked in
save order, with the current section content as the newest revision and
main's head. That is real editing history — it is what made the
lost-widgets incident reconstructable — and it would be perverse to throw
it away while building a feature whose entire point is keeping it.

Guarded on table existence: sequential revision ids collide across branches
here, and this hook runs before every rollout.

Revision ID: 020
Revises: 019
"""
import hashlib
import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "020"
down_revision: Union[str, None] = "019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has(bind, table: str) -> bool:
    return sa.inspect(bind).has_table(table)


def upgrade() -> None:
    bind = op.get_bind()
    if not _has(bind, "reports"):
        # Fresh build: create_all makes these from the models.
        return

    if not _has(bind, "doc_revisions"):
        op.create_table(
            "doc_revisions",
            sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=False),
                      primary_key=True),
            sa.Column("report_id", sa.dialects.postgresql.UUID(as_uuid=False),
                      sa.ForeignKey("reports.id", ondelete="CASCADE"),
                      nullable=False),
            sa.Column("parent_id", sa.dialects.postgresql.UUID(as_uuid=False),
                      sa.ForeignKey("doc_revisions.id", ondelete="SET NULL"),
                      nullable=True),
            sa.Column("content_json", sa.dialects.postgresql.JSONB,
                      nullable=False),
            sa.Column("content_hash", sa.Text, nullable=False),
            sa.Column("author_id", sa.dialects.postgresql.UUID(as_uuid=False),
                      sa.ForeignKey("users.id"), nullable=True),
            sa.Column("author_kind", sa.Text, nullable=False,
                      server_default="human"),
            sa.Column("created_at",
                      sa.dialects.postgresql.TIMESTAMP(timezone=True),
                      nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_doc_revisions_report_created", "doc_revisions",
                        ["report_id", "created_at"])

    if not _has(bind, "doc_branches"):
        op.create_table(
            "doc_branches",
            sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=False),
                      primary_key=True),
            sa.Column("report_id", sa.dialects.postgresql.UUID(as_uuid=False),
                      sa.ForeignKey("reports.id", ondelete="CASCADE"),
                      nullable=False),
            sa.Column("owner_id", sa.dialects.postgresql.UUID(as_uuid=False),
                      sa.ForeignKey("users.id", ondelete="CASCADE"),
                      nullable=True),
            sa.Column("head_revision_id",
                      sa.dialects.postgresql.UUID(as_uuid=False),
                      sa.ForeignKey("doc_revisions.id"), nullable=False),
            sa.Column("base_revision_id",
                      sa.dialects.postgresql.UUID(as_uuid=False),
                      sa.ForeignKey("doc_revisions.id"), nullable=True),
            sa.Column("updated_at",
                      sa.dialects.postgresql.TIMESTAMP(timezone=True),
                      nullable=False, server_default=sa.func.now()),
        )
        # A NULL owner cannot live in a primary key, so the two rules —
        # one main per article, one draft per editor — are partial indexes.
        op.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_doc_branches_main "
            "ON doc_branches (report_id) WHERE owner_id IS NULL"
        )
        op.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_doc_branches_draft "
            "ON doc_branches (report_id, owner_id) WHERE owner_id IS NOT NULL"
        )

    _backfill(bind)


def _backfill(bind) -> None:
    """Carry every article's existing history into the revision chain."""
    if not _has(bind, "sections"):
        return
    already = bind.execute(sa.text("SELECT count(*) FROM doc_branches")).scalar()
    if already:
        return

    rows = bind.execute(sa.text(
        "SELECT s.id, s.report_id, s.content_json, s.updated_at, r.created_by "
        "FROM sections s JOIN reports r ON r.id = s.report_id "
        "ORDER BY s.report_id, s.sort_order"
    )).fetchall()

    seen_reports: set[str] = set()
    for section_id, report_id, content, updated_at, created_by in rows:
        # One document per article: if an article somehow still carries
        # more than one section, the first is the document and the rest
        # are legacy noise that the collapse already stopped producing.
        if report_id in seen_reports:
            continue
        seen_reports.add(report_id)

        parent = None
        history = bind.execute(sa.text(
            "SELECT content_json, saved_by, saved_at FROM section_versions "
            "WHERE section_id = :sid ORDER BY saved_at ASC"
        ), {"sid": section_id}).fetchall()
        for old_content, saved_by, saved_at in history:
            parent = _insert_revision(bind, report_id, parent, old_content,
                                      saved_by, saved_at)
        head = _insert_revision(bind, report_id, parent, content,
                                created_by, updated_at)
        bind.execute(sa.text(
            "INSERT INTO doc_branches "
            "  (id, report_id, owner_id, head_revision_id, base_revision_id) "
            "VALUES (gen_random_uuid(), :rid, NULL, :head, :head)"
        ), {"rid": report_id, "head": head})


def _canonical(content) -> str:
    """The exact bytes the service hashes.

    Sorted keys, no incidental whitespace — the same canonical form
    ``ReportService._hash`` uses. If the two disagreed, a save of
    unchanged content after this migration would look like a change and
    manufacture a revision for it.
    """
    if isinstance(content, str):
        content = json.loads(content)
    return json.dumps(content, sort_keys=True, separators=(",", ":"))


def _insert_revision(bind, report_id, parent, content, author, created_at):
    """One revision. The hash is computed here rather than in SQL: the
    driver types a parameter from its first use, so a value cast to jsonb
    cannot also be handed to convert_to() for hashing."""
    payload = _canonical(content)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return bind.execute(sa.text(
        "INSERT INTO doc_revisions "
        "  (id, report_id, parent_id, content_json, content_hash, "
        "   author_id, author_kind, created_at) "
        "VALUES (gen_random_uuid(), :rid, :parent, CAST(:content AS jsonb), "
        "        :digest, :author, 'human', coalesce(:created_at, now())) "
        "RETURNING id"
    ), {
        "rid": report_id, "parent": parent, "content": payload,
        "digest": digest, "author": author, "created_at": created_at,
    }).scalar()


def downgrade() -> None:
    bind = op.get_bind()
    if _has(bind, "doc_branches"):
        op.drop_table("doc_branches")
    if _has(bind, "doc_revisions"):
        op.drop_table("doc_revisions")
