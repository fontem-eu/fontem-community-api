"""PostgreSQL repository for the Briefings feed."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.feed import EVERYWHERE, FeedItem, FeedRun, Watch
# named_queries and query_group_members are joined in _RANK_SQL by name
# rather than by model — the window function has no ORM equivalent worth
# writing — so they are deliberately not imported here.
from src.infra.postgres.models import FeedItemModel, FeedRunModel, WatchModel
from src.repositories.feed_repository import FeedRepository

# The ranking query. Expressed as SQL rather than assembled in the ORM because
# it is a window function over a join, and the per-week partition is the whole
# point: a flat LIMIT would let one busy week crowd out every other.
#
# Region matching is a PREFIX test — a watcher who picked 'PT' wants PT111 and
# PT1A0 — so it unnests the item's regions rather than using array overlap,
# which would only match exact codes.
_RANK_SQL = text("""
SELECT id, query_id, item_id, item_time, nuts, rank_value, title, link, summary,
       first_seen_at
FROM (
  SELECT fi.*,
         row_number() OVER (
           PARTITION BY date_trunc('week', fi.item_time)
           ORDER BY fi.rank_value DESC NULLS LAST, fi.item_time DESC, fi.item_id
         ) AS rn
  FROM feed_items fi
  JOIN query_group_members m ON m.query_id = fi.query_id
  JOIN named_queries nq ON nq.id = fi.query_id
  WHERE m.group_id = :group_id
    AND nq.status = 'published'
    AND fi.item_time >= :cutoff
    AND (
      :everywhere
      OR EXISTS (
        SELECT 1 FROM unnest(fi.nuts) AS region
        WHERE region LIKE ANY(:prefixes)
      )
    )
) ranked
WHERE rn <= :per_week
ORDER BY item_time DESC, rank_value DESC NULLS LAST
""")


class PgFeedRepository(FeedRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── items ────────────────────────────────────────────────
    @staticmethod
    def _item_to_domain(row) -> FeedItem:
        return FeedItem(
            id=row.id, query_id=row.query_id, item_id=row.item_id,
            item_time=row.item_time, nuts=list(row.nuts or []),
            rank_value=float(row.rank_value) if row.rank_value is not None else None,
            title=row.title, link=row.link, summary=row.summary,
            first_seen_at=row.first_seen_at,
        )

    async def upsert_items(self, items: list[FeedItem]) -> int:
        if not items:
            return 0
        now = datetime.now(timezone.utc)
        payload = [{
            "id": item.id or str(uuid4()),
            "query_id": item.query_id,
            "item_id": item.item_id,
            "item_time": item.item_time,
            "nuts": list(item.nuts or []),
            "rank_value": item.rank_value,
            "title": item.title,
            "link": item.link,
            "summary": item.summary,
            "first_seen_at": now,
        } for item in items]

        # DO NOTHING, deliberately. An item we have already seen keeps its
        # original first_seen_at — that column is the system's only ingestion
        # timestamp, and one that moves on every re-scan is not a timestamp.
        stmt = pg_insert(FeedItemModel).values(payload).on_conflict_do_nothing(
            constraint="feed_items_query_item_unique",
        )
        result = await self._session.execute(stmt)
        await self._session.commit()
        return int(result.rowcount or 0)

    async def rank_items(
        self, group_id: str, nuts: list[str], volume_per_week: int, weeks: int,
    ) -> list[FeedItem]:
        regions = list(nuts or [EVERYWHERE])
        everywhere = EVERYWHERE in regions
        cutoff = datetime.now(timezone.utc) - timedelta(weeks=max(1, weeks))
        rows = (await self._session.execute(_RANK_SQL, {
            "group_id": group_id,
            "cutoff": cutoff,
            "everywhere": everywhere,
            # LIKE patterns, so 'PT' matches PT111. Never an empty list:
            # ANY(ARRAY[]) is false, and the everywhere flag short-circuits first.
            "prefixes": [f"{r}%" for r in regions] or ["%"],
            "per_week": max(1, volume_per_week),
        })).all()
        return [self._item_to_domain(r) for r in rows]

    # ── runs ─────────────────────────────────────────────────
    @staticmethod
    def _run_to_domain(row: FeedRunModel) -> FeedRun:
        return FeedRun(
            id=row.id, query_id=row.query_id, started_at=row.started_at,
            finished_at=row.finished_at, status=row.status, partitions=row.partitions,
            items_seen=row.items_seen, items_new=row.items_new,
            truncated_partitions=row.truncated_partitions,
            error_message=row.error_message,
        )

    async def start_run(self, run: FeedRun) -> FeedRun:
        model = FeedRunModel(
            id=run.id or str(uuid4()), query_id=run.query_id,
            started_at=datetime.now(timezone.utc), status="running",
        )
        self._session.add(model)
        await self._session.commit()
        return self._run_to_domain(model)

    async def finish_run(self, run: FeedRun) -> FeedRun:
        model = await self._session.get(FeedRunModel, run.id)
        if model is None:
            return run
        model.finished_at = run.finished_at or datetime.now(timezone.utc)
        model.status = run.status
        model.partitions = run.partitions
        model.items_seen = run.items_seen
        model.items_new = run.items_new
        model.truncated_partitions = run.truncated_partitions
        model.error_message = run.error_message
        await self._session.commit()
        return self._run_to_domain(model)

    async def recent_runs(self, query_id: str, limit: int = 10) -> list[FeedRun]:
        stmt = (select(FeedRunModel)
                .where(FeedRunModel.query_id == query_id)
                .order_by(FeedRunModel.started_at.desc())
                .limit(limit))
        rows = (await self._session.execute(stmt)).scalars().all()
        return [self._run_to_domain(r) for r in rows]

    # ── watches ──────────────────────────────────────────────
    @staticmethod
    def _watch_to_domain(row: WatchModel) -> Watch:
        return Watch(
            id=row.id, user_id=row.user_id, group_id=row.group_id,
            nuts=list(row.nuts or []), volume_per_week=row.volume_per_week,
            token=row.token, created_at=row.created_at, updated_at=row.updated_at,
            last_polled_at=row.last_polled_at,
        )

    async def create_watch(self, watch: Watch) -> Watch:
        model = WatchModel(
            id=watch.id or str(uuid4()), user_id=watch.user_id, group_id=watch.group_id,
            nuts=list(watch.nuts or [EVERYWHERE]),
            volume_per_week=watch.volume_per_week, token=watch.token,
        )
        self._session.add(model)
        await self._session.commit()
        return self._watch_to_domain(model)

    async def get_watch(self, watch_id: str) -> Watch | None:
        row = await self._session.get(WatchModel, watch_id)
        return self._watch_to_domain(row) if row else None

    async def get_watch_by_token(self, token: str) -> Watch | None:
        stmt = select(WatchModel).where(WatchModel.token == token)
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return self._watch_to_domain(row) if row else None

    async def find_watch(self, user_id: str, group_id: str) -> Watch | None:
        stmt = select(WatchModel).where(
            WatchModel.user_id == user_id, WatchModel.group_id == group_id)
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return self._watch_to_domain(row) if row else None

    async def list_watches(self, user_id: str) -> list[Watch]:
        stmt = (select(WatchModel).where(WatchModel.user_id == user_id)
                .order_by(WatchModel.created_at.desc()))
        rows = (await self._session.execute(stmt)).scalars().all()
        return [self._watch_to_domain(r) for r in rows]

    async def update_watch(self, watch: Watch) -> Watch:
        model = await self._session.get(WatchModel, watch.id)
        if model is None:
            return watch
        model.nuts = list(watch.nuts or [EVERYWHERE])
        model.volume_per_week = watch.volume_per_week
        model.updated_at = datetime.now(timezone.utc)
        await self._session.commit()
        return self._watch_to_domain(model)

    async def delete_watch(self, watch_id: str) -> None:
        await self._session.execute(delete(WatchModel).where(WatchModel.id == watch_id))
        await self._session.commit()

    async def mark_polled(self, watch_id: str, when: datetime) -> None:
        model = await self._session.get(WatchModel, watch_id)
        if model is not None:
            model.last_polled_at = when
            await self._session.commit()
