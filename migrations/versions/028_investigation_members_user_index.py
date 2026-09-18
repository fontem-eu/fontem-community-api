"""Index investigation memberships by user.

GET /investigations lists the investigations a user belongs to, which
filters investigation_members on user_id. The primary key leads with
investigation_id, so every such lookup read the whole table.

Guarded twice, like 027: a database without the table has nothing to
index, and one that already has the index (a hand-applied fix, a repeated
run) is left as it is.

Revision ID: 028
Revises: 027
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "028"
down_revision: Union[str, None] = "027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TABLE = "investigation_members"


def upgrade() -> None:
    if not sa.inspect(op.get_bind()).has_table(_TABLE):
        return
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_investigation_members_user "
        "ON investigation_members (user_id)"
    )


def downgrade() -> None:
    if not sa.inspect(op.get_bind()).has_table(_TABLE):
        return
    op.execute("DROP INDEX IF EXISTS ix_investigation_members_user")
