"""Structured detail on a feed item, beside the prose title.

A briefing card had one string to work with: a sentence the query composed,
"A awarded 34769555 EUR to B and 1 other". That reads well in an Atom
reader and badly on a card, where the parts want different weight — the
contract's own title is what a reader recognises, the value is what they
compare, and neither should be buried in the middle of a paragraph.

Splitting the sentence back apart in the browser was the alternative. It
would be guesswork against a string the query is free to change, and it
would break outright the first time a query is authored in another
language, which `named_queries.lang` already anticipates.

So the query emits the parts and this column carries them. Nullable, and
deliberately not backfilled: `upsert_items` is ON CONFLICT DO NOTHING so
that `first_seen_at` cannot move, which means existing rows keep NULL
until a query re-materialises them under a new id. Every reader therefore
has to render without facets, and the card does.

Revision ID: 023
Revises: 022
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "023"
down_revision: Union[str, None] = "022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Guarded: testing may already carry the column from an earlier apply.
    conn = op.get_bind()
    exists = conn.execute(sa.text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = 'feed_items' AND column_name = 'facets'"
    )).first()
    if not exists:
        op.add_column(
            "feed_items",
            sa.Column("facets", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        )


def downgrade() -> None:
    conn = op.get_bind()
    exists = conn.execute(sa.text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = 'feed_items' AND column_name = 'facets'"
    )).first()
    if exists:
        op.drop_column("feed_items", "facets")
