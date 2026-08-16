"""Briefings — the materialised feed, its runs, and who watches it.

A BRIEFING is the public name for a query group. A watcher picks one, picks
their regions, and says how much they want ("about ten a week"); the platform
gives them an Atom feed.

The three types here are the spine:

* :class:`FeedItem` — one materialised result row. Its ``first_seen_at`` is
  the only ingestion timestamp anywhere in the system: no upstream store
  records when *we* learned a fact, so this table is where that becomes
  knowable.
* :class:`FeedRun` — what one execution of one query did, including whether
  any partition came back truncated. Silent truncation is precisely the
  failure this design keeps refusing to accept.
* :class:`Watch` — a subscription, expressed as a VOLUME rather than a
  threshold, because the same threshold cannot serve a NUTS-3 region and the
  whole EU.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

RUN_STATUSES = ("running", "ok", "error")

# A watcher asking for more than this is asking for a database dump, not a
# briefing; below one, they are asking for nothing.
MIN_VOLUME = 1
MAX_VOLUME = 200
DEFAULT_VOLUME = 10

# 'EU' in a region list means "everywhere" rather than a NUTS prefix, so a
# watcher can say "all of it" without enumerating 27 countries.
EVERYWHERE = "EU"


@dataclass
class FeedItem:  # pylint: disable=too-many-instance-attributes
    id: str | None = None
    query_id: str = ""
    item_id: str = ""
    item_time: datetime | None = None
    nuts: list[str] = field(default_factory=list)
    rank_value: float | None = None
    title: str = ""
    link: str = ""
    summary: str = ""
    first_seen_at: datetime | None = None


@dataclass
class FeedRun:  # pylint: disable=too-many-instance-attributes
    id: str | None = None
    query_id: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None
    status: str = "running"
    partitions: int = 0
    items_seen: int = 0
    items_new: int = 0
    truncated_partitions: int = 0
    error_message: str | None = None

    def summary_line(self) -> str:
        """One line an operator can read without opening the table."""
        base = (f"{self.status}: {self.partitions} partitions, "
                f"{self.items_seen} seen, {self.items_new} new")
        if self.truncated_partitions:
            base += f", {self.truncated_partitions} TRUNCATED"
        if self.error_message:
            base += f" — {self.error_message}"
        return base


@dataclass
class Watch:  # pylint: disable=too-many-instance-attributes
    id: str | None = None
    user_id: str = ""
    group_id: str = ""
    nuts: list[str] = field(default_factory=lambda: [EVERYWHERE])
    volume_per_week: int = DEFAULT_VOLUME
    token: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None
    last_polled_at: datetime | None = None

    def watches_everywhere(self) -> bool:
        return EVERYWHERE in (self.nuts or [])
