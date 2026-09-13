"""When each account last signed in.

The admin user directory shows when every account last logged in, and
nothing recorded it. `users` has `created_at` and a failed-login counter;
refresh-token families carry `rotated_at`, which moves on every silent
token refresh and so means "last seen", not "last login". A column
stamped at the one seam every sign-in path goes through
(`auth._issue_session`) is the only honest source.

No backfill. There is no history to derive it from, and a guessed value
would be indistinguishable from a real one: existing accounts read NULL,
"not recorded yet", until they next sign in.

Guarded, so a database that already has the column — a hand-applied fix,
or a repeated run — is left as it is.

Revision ID: 026
Revises: 025
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "026"
down_revision: Union[str, None] = "025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    return op.get_bind().execute(sa.text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema = current_schema() "
        "AND table_name = :table AND column_name = :column"
    ), {"table": table, "column": column}).first() is not None


def upgrade() -> None:
    if not _has_column("users", "last_login_at"):
        op.add_column(
            "users",
            sa.Column("last_login_at", sa.TIMESTAMP(timezone=True), nullable=True),
        )


def downgrade() -> None:
    if _has_column("users", "last_login_at"):
        op.drop_column("users", "last_login_at")
