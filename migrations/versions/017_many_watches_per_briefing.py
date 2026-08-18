"""watches: a briefing can be watched several times, differently

Revision ID: 017
Revises: 016
Create Date: 2026-08-17

016 put UNIQUE (user_id, group_id) on watches, which quietly asserted that a
person has at most one opinion about a briefing. That is wrong. The real case
is ordinary: fifty items a week from Coimbra, ten from Portugal, ten from the
whole EU — the same briefing three times, at three scopes, because a local
award and a European one are different kinds of news to the same reader.

So the constraint goes. A watch becomes an independent subscription:
(user, briefing, regions, volume) with its own feed token, and a user has 1..N
of them per briefing rather than 0..1.

Nothing else about the row changes, and no data moves — every existing watch
is still a valid single subscription. This is purely a widening, which is why
it is safe to apply while the old code is still serving: the old code creates
at most one watch per briefing and simply never exercises the new freedom.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "017"
down_revision: Union[str, None] = "016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("watches_user_group_unique", "watches", type_="unique")
    # The read path is "every watch this person has", and it is now genuinely
    # a list rather than a lookup, so the ordering it renders in is worth an
    # index rather than a sort.
    op.create_index("ix_watches_user_created", "watches", ["user_id", "created_at"])


def downgrade() -> None:
    """Deliberately lossy, and it says so.

    Restoring the constraint is impossible while a user holds two watches on
    one briefing, so the downgrade keeps the OLDEST of each set and deletes
    the rest. Oldest rather than newest because it is the one whose feed URL
    has been in someone's reader the longest.
    """
    op.drop_index("ix_watches_user_created", table_name="watches")
    op.execute("""
        DELETE FROM watches w
        USING watches keep
        WHERE w.user_id = keep.user_id
          AND w.group_id = keep.group_id
          AND keep.created_at < w.created_at
    """)
    op.create_unique_constraint("watches_user_group_unique", "watches",
                                ["user_id", "group_id"])
