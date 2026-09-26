"""Index Data Studio projects for the paged owner list.

GET /studio/projects now pages newest-first on (updated_at, id) for one
owner, and the Studio rail draws that list on every page of the app. With no
index, each page sorted the owner's whole set of projects to return thirty;
the DAST account, which accumulates projects on purpose as a load canary,
holds 1,147 of them.

The column order matches PgDataProjectRepository._page, so a page is an
index range scan and the ``before`` cursor is a seek.

Guarded twice, like 027 and 028: a database without the table has nothing to
index, and one that already has the index (created by create_all on a fresh
build, a hand-applied fix, a repeated run) is left as it is.

Revision ID: 029
Revises: 028
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "029"
down_revision: Union[str, None] = "028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TABLE = "data_projects"


def upgrade() -> None:
    if not sa.inspect(op.get_bind()).has_table(_TABLE):
        return
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_data_projects_owner_page "
        "ON data_projects (created_by, updated_at DESC, id DESC)"
    )


def downgrade() -> None:
    if not sa.inspect(op.get_bind()).has_table(_TABLE):
        return
    op.execute("DROP INDEX IF EXISTS ix_data_projects_owner_page")
