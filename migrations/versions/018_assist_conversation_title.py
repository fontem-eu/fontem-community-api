"""Give a conversation a title, so a list of them can be read.

Multiple chats need something to show in a switcher. The conversation key is
not it: it is ``chat:<uuid>`` or ``report:<uuid>``, opaque by design and
meaningless to the person choosing between them.

Nullable rather than defaulted. A conversation has no title until it has a
first message to take one from, and "Untitled" rows are worse than an absent
title the UI can fill in from the opening question.

Guarded on column existence rather than assuming a fresh build: sequential
revision ids collide across branches here, and a migration that has already
been applied under another id must not fail the deploy. The PreSync hook runs
this before every rollout.

Revision ID: 018
Revises: 017
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "018"
down_revision: Union[str, None] = "017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "assist_conversations"
_COLUMN = "title"


def _has_column(bind, table: str, column: str) -> bool:
    return column in {
        c["name"] for c in sa.inspect(bind).get_columns(table)
    }


def upgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table(_TABLE):
        # Nothing to alter on a database that predates the assistant tables;
        # create_all will build the column from the model.
        return
    if _has_column(bind, _TABLE, _COLUMN):
        return
    op.add_column(_TABLE, sa.Column(_COLUMN, sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table(_TABLE):
        return
    if not _has_column(bind, _TABLE, _COLUMN):
        return
    op.drop_column(_TABLE, _COLUMN)
