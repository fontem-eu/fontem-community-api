"""Keep a rolling summary of what fell out of the continuity window.

When a conversation outgrows the model's context, the oldest turns are
dropped. Dropping them silently is what makes a long chat start contradicting
decisions taken earlier in itself. The summary is what those turns become.

Two columns, and the second is the one that makes it *rolling*:
``summary_through`` records the last message already folded in, so the next
overflow summarises only what has fallen off since rather than re-reading the
whole conversation. Without it the cost grows with the conversation, which is
exactly the thing being avoided.

Both nullable: a conversation that has never overflowed has no summary, and
on a large-context model that is every conversation. Absent is the normal
state, not a missing value.

Guarded on column existence rather than assuming a fresh build: sequential
revision ids collide across branches here, and a migration already applied
under another id must not fail the deploy. The PreSync hook runs this before
every rollout.

Revision ID: 019
Revises: 018
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "019"
down_revision: Union[str, None] = "018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "assist_conversations"
_COLUMNS = {
    "summary": sa.Text(),
    "summary_through": sa.Text(),
}


def _columns(bind) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table(_TABLE):
        # Predates the assistant tables; create_all builds these from the model.
        return
    existing = _columns(bind)
    for name, type_ in _COLUMNS.items():
        if name not in existing:
            op.add_column(_TABLE, sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table(_TABLE):
        return
    existing = _columns(bind)
    for name in _COLUMNS:
        if name in existing:
            op.drop_column(_TABLE, name)
