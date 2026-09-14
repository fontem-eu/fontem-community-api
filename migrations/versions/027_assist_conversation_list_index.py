"""The switcher lists a user's conversations a page at a time.

GET /assist/conversations returned every conversation a user had ever held,
sorted in memory: 690 rows and 215 KiB for the account the e2e suite shares,
growing with every chat. It now pages newest activity first, with a keyset
cursor on (updated_at, id). This index is what lets each page be read in
order and stop after `limit` rows instead of sorting the user's whole history.

Guarded twice, like 018 and 019. A fresh `upgrade head` has no such table to
index: 002 creates it and 008 drops it again, so only deployed databases (stamped
at 008, table intact) get the index here. And a database that already has it —
a hand-applied fix, or a repeated run — is left as it is.

Revision ID: 027
Revises: 026
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "027"
down_revision: Union[str, None] = "026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TABLE = "assist_conversations"


def upgrade() -> None:
    if not sa.inspect(op.get_bind()).has_table(_TABLE):
        return
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_assist_conv_user_updated "
        "ON assist_conversations (user_id, updated_at DESC, id DESC)"
    )


def downgrade() -> None:
    if not sa.inspect(op.get_bind()).has_table(_TABLE):
        return
    op.execute("DROP INDEX IF EXISTS ix_assist_conv_user_updated")
