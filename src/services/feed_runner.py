"""Executes published feed queries and materialises their results.

Why this exists at all: Atom readers poll every 15-60 minutes and there may be
many of them. Running an arbitrary graph query per poll is impossible, so the
runner executes on its own cadence and the feed endpoint only ever reads rows.

Three things it is careful about.

**It never truncates silently.** The read-only proxy caps every response at
1000 rows. Measured on prod, a single EU-wide day of procurement returned 741
rows once and 1000 (capped) the next — so paging by day alone is not enough.
The runner therefore splits ON TRUNCATION: it asks for a whole day, and if the
answer came back at the cap it re-asks country by country. That is
self-tuning — a quiet query costs one request per day, a busy one costs 27 —
and any partition still truncated after splitting is COUNTED on the run row
rather than shrugged off.

**It writes the ingestion timestamp nobody else has.** No upstream store
records when we learned a fact. ``first_seen_at`` is set the first time an
item_id is seen and never moves, which is what makes "new since you last
looked" a question we can actually answer.

**It re-reads a deliberately overlapping window.** ``since`` is a cost bound,
not a correctness boundary: correctness comes from the unique constraint on
(query_id, item_id). Re-scanning a lag window and letting the upsert discard
what we already have is what catches a late arrival — an upstream delay, a
pipeline outage, a correction — without needing the source to tell us.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from loguru import logger

from src.domain.feed import FeedItem, FeedRun
from src.domain.named_query import NamedQuery
from src.repositories.feed_repository import FeedRepository
from src.repositories.named_query_repository import NamedQueryRepository
from src.services import feed_contract
from src.services.query_executor import ExecResult, QueryExecutor

# The NUTS country prefixes the runner splits on. Written out rather than
# derived from the data: a country that has published nothing yet still needs
# to be asked, or its first contract is invisible until someone notices.
EU_COUNTRIES = (
    "AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "EL", "ES", "FI", "FR",
    "HR", "HU", "IE", "IT", "LT", "LU", "LV", "MT", "NL", "PL", "PT", "RO",
    "SE", "SI", "SK",
)

# How far back each run re-reads. Generous on purpose — see the module
# docstring on late arrivals. Cheap because the upsert discards duplicates.
DEFAULT_LAG_DAYS = 7

# Mirrors the proxy's own cap. A response of exactly this size means rows were
# dropped, which is the signal to split the partition.
PROXY_ROW_CAP = 1000


@dataclass
class Partition:
    """One request the runner will make: a day, and a region scope."""

    day: datetime
    nuts: list[str]

    @property
    def label(self) -> str:
        return f"{self.day.date().isoformat()}/{','.join(self.nuts)}"


class FeedRunner:
    def __init__(
        self,
        queries: NamedQueryRepository,
        feed: FeedRepository,
        executor: QueryExecutor,
        lag_days: int = DEFAULT_LAG_DAYS,
    ) -> None:
        self._queries = queries
        self._feed = feed
        self._executor = executor
        self._lag_days = lag_days

    async def run_all(self) -> list[FeedRun]:
        """Run every published query. One bad query must not stop the rest."""
        published = await self._queries.list_queries(status="published")
        runs = []
        for query in published:
            try:
                runs.append(await self.run_query(query))
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.exception("feed run failed for {}: {}", query.slug, exc)
        return runs

    async def run_query(self, query: NamedQuery, now: datetime | None = None) -> FeedRun:
        now = now or datetime.now(timezone.utc)
        run = await self._feed.start_run(FeedRun(query_id=query.id))
        seen = new = truncated = 0
        partitions = 0
        error: str | None = None

        try:
            for day in self._days(now):
                results = await self._fetch_day(query, day)
                for partition, result in results:
                    partitions += 1
                    if result.error:
                        # One partition failing is not the run failing: the
                        # rest of the window is still worth materialising.
                        logger.warning("partition {} of {} failed: {}",
                                       partition.label, query.slug, result.error)
                        error = error or result.error
                        continue
                    if result.truncated or result.row_count >= PROXY_ROW_CAP:
                        truncated += 1
                        logger.warning(
                            "partition {} of {} still truncated after splitting — "
                            "rows were dropped", partition.label, query.slug)
                    items = list(self._to_items(query, result))
                    seen += len(items)
                    new += await self._feed.upsert_items(items)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            error = str(exc)[:500]

        run.partitions = partitions
        run.items_seen = seen
        run.items_new = new
        run.truncated_partitions = truncated
        run.status = "error" if error and seen == 0 else "ok"
        run.error_message = error
        run.finished_at = datetime.now(timezone.utc)
        return await self._feed.finish_run(run)

    def _days(self, now: datetime) -> list[datetime]:
        start = now - timedelta(days=self._lag_days)
        return [start + timedelta(days=i) for i in range(self._lag_days + 1)]

    async def _fetch_day(self, query: NamedQuery, day: datetime):
        """A whole day if it fits, otherwise the same day country by country.

        Splitting only on truncation keeps a quiet query at one request per
        day while a busy one pays for 27 — without anyone having to predict
        in advance which it is.
        """
        whole = Partition(day=day, nuts=["EU"])
        result = await self._execute(query, whole)
        if result.error or not self._is_capped(result):
            return [(whole, result)]

        logger.info("splitting {} for {} — came back at the row cap",
                    whole.label, query.slug)
        out = []
        for country in EU_COUNTRIES:
            part = Partition(day=day, nuts=[country])
            out.append((part, await self._execute(query, part)))
        return out

    @staticmethod
    def _is_capped(result: ExecResult) -> bool:
        return bool(result.truncated) or result.row_count >= PROXY_ROW_CAP

    async def _execute(self, query: NamedQuery, partition: Partition) -> ExecResult:
        params = feed_contract.sample_params(query.params)
        params["nuts"] = list(partition.nuts)
        # One day's worth: everything published after the previous midnight.
        params["since"] = (partition.day - timedelta(days=1)).isoformat()
        return await self._executor.run(query.lang, query.query, params)

    @staticmethod
    def _to_items(query: NamedQuery, result: ExecResult):
        cols = list(result.columns or [])
        try:
            idx = {name: cols.index(name) for name in feed_contract.REQUIRED_COLUMNS}
        except ValueError:
            # A published query whose shape drifted. Skip rather than write
            # half-formed items; validation is where this gets reported.
            logger.error("query {} no longer projects the contract columns: {}",
                         query.slug, cols)
            return
        summary_at = cols.index("summary") if "summary" in cols else None

        for row in result.rows or []:
            item_time = _as_datetime(row[idx["item_time"]])
            if item_time is None:
                continue
            yield FeedItem(
                query_id=query.id,
                item_id=str(row[idx["item_id"]]),
                item_time=item_time,
                nuts=_as_list(row[idx["nuts"]]),
                rank_value=_as_number(row[idx["rank_value"]]),
                title=str(row[idx["title"]] or "")[:1000],
                link=str(row[idx["link"]] or "")[:2000],
                summary=str(row[summary_at] or "")[:4000] if summary_at is not None else "",
            )


def _as_datetime(value) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _as_list(value) -> list[str]:
    """nuts is a list when a thing spans regions, a bare code when it doesn't.

    Both are accepted, because forcing every query to wrap a single region in
    a list would be ceremony for its own sake.
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if v is not None]
    return [str(value)]


def _as_number(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
