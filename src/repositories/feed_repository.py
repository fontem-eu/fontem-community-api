"""Abstract repository for the Briefings feed."""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from src.domain.feed import FeedItem, FeedRun, Watch


class FeedRepository(ABC):
    # ── items ────────────────────────────────────────────────
    @abstractmethod
    async def upsert_items(self, items: list[FeedItem]) -> int:
        """Insert what is new, ignore what is already there. Returns the count
        of genuinely new rows.

        Never updates ``first_seen_at`` on an item it has seen before — that
        column is the system's only ingestion timestamp, and a value that
        moves is not a timestamp.
        """

    @abstractmethod
    async def rank_items(
        self,
        group_id: str,
        nuts: list[str],
        volume_per_week: int,
        weeks: int,
    ) -> list[FeedItem]:
        """The top ``volume_per_week`` items of each week, for a briefing, in
        the given regions, over the last ``weeks`` weeks.

        Per-week rather than a flat limit so a single busy week cannot crowd
        out every other, and a quiet week simply returns fewer.
        """

    # ── runs ─────────────────────────────────────────────────
    @abstractmethod
    async def start_run(self, run: FeedRun) -> FeedRun: ...

    @abstractmethod
    async def finish_run(self, run: FeedRun) -> FeedRun: ...

    @abstractmethod
    async def recent_runs(self, query_id: str, limit: int = 10) -> list[FeedRun]: ...

    # ── watches ──────────────────────────────────────────────
    @abstractmethod
    async def create_watch(self, watch: Watch) -> Watch: ...

    @abstractmethod
    async def get_watch(self, watch_id: str) -> Watch | None: ...

    @abstractmethod
    async def get_watch_by_token(self, token: str) -> Watch | None: ...

    @abstractmethod
    async def find_watch(self, user_id: str, group_id: str) -> Watch | None: ...

    @abstractmethod
    async def list_watches(self, user_id: str) -> list[Watch]: ...

    @abstractmethod
    async def update_watch(self, watch: Watch) -> Watch: ...

    @abstractmethod
    async def delete_watch(self, watch_id: str) -> None: ...

    @abstractmethod
    async def mark_polled(self, watch_id: str, when: datetime) -> None: ...
