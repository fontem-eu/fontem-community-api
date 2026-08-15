"""Record the previous refresh-token hash, so a race is not treated as theft.

Rotation swaps the family's current hash on every /auth/refresh. Until now
nothing remembered what the hash had just been, which had two consequences:

  * A second tab refreshing moments later offered a token that was no
    longer current, matched nothing, and was told "unknown refresh token".
    The SPA reads that as a dead session and sends the user to /login. Two
    tabs could log each other out — observed as 14 × `POST /auth/refresh
    → 401` in a single e2e run.
  * The reuse detection the auth module documents ("we record the previous
    hash on the family one-step-back and check the offered hash against
    it; a hit there is the unambiguous reuse signal and we revoke") was
    never implemented. The strictness was real; the security property it
    was supposed to buy was not.

One nullable column supports both: inside a short grace window a hit means
a concurrent refresh, and outside it means a replayed token.

Revision ID: 015
Revises: 014
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "015"
down_revision: Union[str, None] = "014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable, with no backfill: existing families simply have no previous
    # hash until their next rotation, which is the truth. Backfilling the
    # current hash would make every live session look like it had just
    # rotated and hand it a grace window it never earned.
    op.add_column(
        "refresh_token_families",
        sa.Column("previous_token_hash", sa.Text(), nullable=True),
    )
    # The grace check looks a family up BY this column, on a table that
    # grows one row per login.
    op.create_index(
        "ix_refresh_families_previous_hash",
        "refresh_token_families",
        ["previous_token_hash"],
    )


def downgrade() -> None:
    op.drop_index("ix_refresh_families_previous_hash",
                  table_name="refresh_token_families")
    op.drop_column("refresh_token_families", "previous_token_hash")
