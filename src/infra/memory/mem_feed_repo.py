"""In-memory Briefings feed for unit tests.

Mirrors the Postgres ranking semantics closely enough to be worth testing
against: per-week top-N, prefix region matching, and an ingestion timestamp
that never moves once set.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from src.domain.feed import EVERYWHERE, FeedItem, FeedRun, Watch
from src.repositories.feed_repository import FeedRepository


class InMemoryFeedRepository(FeedRepository):
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], FeedItem] = {}
        self._runs: dict[str, FeedRun] = {}
        self._watches: dict[str, Watch] = {}
        # query_id -> group_ids, so ranking can resolve a briefing's queries
        # without dragging the whole catalogue repo in.
        self.membership: dict[str, list[str]] = {}
        self.published: set[str] = set()

    def all_items(self) -> list[FeedItem]:
        """Everything stored, for tests. A public accessor rather than tests
        reaching into the dict — the storage shape is this class's business."""
        return [deepcopy(i) for i in self._items.values()]

    async def upsert_items(self, items: list[FeedItem]) -> int:
        now = datetime.now(timezone.utc)
        new = 0
        for item in items:
            key = (item.query_id, item.item_id)
            if key in self._items:
                continue          # first_seen_at must not move
            stored = deepcopy(item)
            stored.id = stored.id or str(uuid4())
            stored.first_seen_at = now
            self._items[key] = stored
            new += 1
        return new

    async def rank_items(
        self, group_id: str, nuts: list[str], volume_per_week: int, weeks: int,
    ) -> list[FeedItem]:
        regions = list(nuts or [EVERYWHERE])
        everywhere = EVERYWHERE in regions
        cutoff = datetime.now(timezone.utc) - timedelta(weeks=max(1, weeks))

        def in_scope(item: FeedItem) -> bool:
            if item.query_id not in self.published:
                return False
            if group_id not in self.membership.get(item.query_id, []):
                return False
            if item.item_time is None or item.item_time < cutoff:
                return False
            if everywhere:
                return True
            return any(r.startswith(p) for r in item.nuts for p in regions)

        candidates = [i for i in self._items.values() if in_scope(i)]
        buckets: dict[tuple[int, int], list[FeedItem]] = {}
        for item in candidates:
            buckets.setdefault(item.item_time.isocalendar()[:2], []).append(item)

        picked: list[FeedItem] = []
        for week_items in buckets.values():
            week_items.sort(
                key=lambda i: (-(i.rank_value if i.rank_value is not None else float("-inf")),
                               -i.item_time.timestamp(), i.item_id))
            picked.extend(week_items[:max(1, volume_per_week)])
        picked.sort(key=lambda i: (-i.item_time.timestamp(),
                                   -(i.rank_value if i.rank_value is not None else 0)))
        return [deepcopy(i) for i in picked]

    async def start_run(self, run: FeedRun) -> FeedRun:
        stored = deepcopy(run)
        stored.id = stored.id or str(uuid4())
        stored.started_at = datetime.now(timezone.utc)
        stored.status = "running"
        self._runs[stored.id] = stored
        return deepcopy(stored)

    async def finish_run(self, run: FeedRun) -> FeedRun:
        stored = deepcopy(run)
        stored.finished_at = stored.finished_at or datetime.now(timezone.utc)
        self._runs[stored.id] = stored
        return deepcopy(stored)

    async def recent_runs(self, query_id: str, limit: int = 10) -> list[FeedRun]:
        runs = [deepcopy(r) for r in self._runs.values() if r.query_id == query_id]
        runs.sort(key=lambda r: r.started_at or datetime.min.replace(tzinfo=timezone.utc),
                  reverse=True)
        return runs[:limit]

    async def create_watch(self, watch: Watch) -> Watch:
        stored = deepcopy(watch)
        stored.id = stored.id or str(uuid4())
        now = datetime.now(timezone.utc)
        stored.created_at = stored.updated_at = now
        self._watches[stored.id] = stored
        return deepcopy(stored)

    async def get_watch(self, watch_id: str) -> Watch | None:
        found = self._watches.get(watch_id)
        return deepcopy(found) if found else None

    async def get_watch_by_token(self, token: str) -> Watch | None:
        for watch in self._watches.values():
            if watch.token == token:
                return deepcopy(watch)
        return None

    async def find_watch(self, user_id: str, group_id: str) -> Watch | None:
        for watch in self._watches.values():
            if watch.user_id == user_id and watch.group_id == group_id:
                return deepcopy(watch)
        return None

    async def list_watches(self, user_id: str) -> list[Watch]:
        out = [deepcopy(w) for w in self._watches.values() if w.user_id == user_id]
        return sorted(out, key=lambda w: w.created_at or datetime.min.replace(
            tzinfo=timezone.utc), reverse=True)

    async def update_watch(self, watch: Watch) -> Watch:
        if watch.id not in self._watches:
            return watch
        stored = deepcopy(watch)
        stored.created_at = self._watches[watch.id].created_at
        stored.updated_at = datetime.now(timezone.utc)
        self._watches[watch.id] = stored
        return deepcopy(stored)

    async def delete_watch(self, watch_id: str) -> None:
        self._watches.pop(watch_id, None)

    async def mark_polled(self, watch_id: str, when: datetime) -> None:
        if watch_id in self._watches:
            self._watches[watch_id].last_polled_at = when
