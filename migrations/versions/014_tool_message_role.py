"""assist_messages: 'tool' is a role

Revision ID: 014
Revises: 013
Create Date: 2026-08-13

Tool calls are conversation rows now — one per call, naming the tool and its
arguments — and `ck_assist_msg_role` allowed only 'user' and 'assistant'.

Worth stating why this was not caught earlier: the constraint exists only in
the database. The in-memory repository the unit tests use has no such rule,
so 1064 tests passed while the first real turn in testing failed with

    CheckViolationError: new row for relation "assist_messages"
    violates check constraint "ck_assist_msg_role"

and took the whole turn down with it, because the failed flush poisoned the
transaction the rest of the turn was writing in.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "014"
down_revision: Union[str, None] = "013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_assist_messages() -> bool:
    """Deployed databases have this table; a freshly built one does not.

    008 is a baseline catch-up: it DROPS assist_conversations and
    assist_messages, because the models had replaced them at the time. Every
    real environment was `alembic stamp 008`-ed rather than upgraded, so they
    kept the tables and the app has been using them ever since — see 008's
    own docstring, which calls it a known divergence.

    The consequence for anything touching these tables: a migration that
    assumes they exist works everywhere that matters and fails on a fresh
    build, which is exactly the database CI creates.
    """
    return sa.inspect(op.get_bind()).has_table("assist_messages")


def upgrade() -> None:
    if not _has_assist_messages():
        return
    op.drop_constraint("ck_assist_msg_role", "assist_messages", type_="check")
    op.create_check_constraint(
        "ck_assist_msg_role", "assist_messages",
        "role IN ('user', 'assistant', 'tool')",
    )


def downgrade() -> None:
    if not _has_assist_messages():
        return
    # Tool rows would violate the narrower constraint, so they go first.
    # Losing them is the price of going back, and it is the right price:
    # the alternative is a downgrade that fails on any database that has
    # actually been used.
    op.execute("DELETE FROM assist_messages WHERE role = 'tool'")
    op.drop_constraint("ck_assist_msg_role", "assist_messages", type_="check")
    op.create_check_constraint(
        "ck_assist_msg_role", "assist_messages",
        "role IN ('user', 'assistant')",
    )
