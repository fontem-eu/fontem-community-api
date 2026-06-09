"""Repository ABC for the Medium-style clap (flowers) feature.

The cap is enforced atomically inside ``increment`` via the upsert's
WHERE clause — there is no separate read-then-check path that two
concurrent POSTs could race past.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class FlowerRepository(ABC):
    @abstractmethod
    async def get_mine(self, user_id: str, report_id: str) -> int:
        """Return how many flowers ``user_id`` has given ``report_id``.

        Returns 0 when no row exists; never raises on the not-found
        path because "I have not clapped" is a normal state.
        """
        ...

    @abstractmethod
    async def get_total(self, report_id: str) -> int:
        """Return SUM(count) across every user for the report. Anonymous
        callers consume this; signed-in callers use it alongside
        ``get_mine`` to render "you've given X of Y total"."""
        ...

    @abstractmethod
    async def increment(
        self, user_id: str, report_id: str, *, cap: int,
    ) -> int | None:
        """Atomic upsert: +1 to the (user, report) row, returns the new
        ``count`` for that user — or ``None`` when the existing row is
        already at ``cap`` and the write was rejected.

        Implementations MUST enforce the cap inside the same statement
        as the write (e.g. ``ON CONFLICT DO UPDATE ... WHERE count <
        cap``) so two concurrent POSTs from the same user cannot both
        slip past ``cap-1`` and produce ``cap+1``.
        """
        ...
