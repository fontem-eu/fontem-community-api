"""Briefings: the public catalogue, and watching one.

A BRIEFING is the public name for a query group. Watching one is
``(briefing, my regions, how much I want)`` — a volume, never a threshold,
because the same threshold cannot serve a NUTS-3 region and the whole EU. The
ranking that turns a volume into items lives in the repository, against the
materialised table, so it can be recomputed for whatever regions the watcher
picked without re-running anything upstream.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone

from src.domain.feed import (
    DEFAULT_VOLUME,
    EVERYWHERE,
    MAX_VOLUME,
    MIN_VOLUME,
    Watch,
)
from src.domain.named_query import QueryGroup
from src.repositories.feed_repository import FeedRepository
from src.repositories.named_query_repository import NamedQueryRepository
from src.services.exceptions import InvalidInput, NotFound, PermissionDenied

# How much history a feed shows. Four weeks so a reader that has been away for
# a fortnight still sees what it missed, without the feed becoming an archive.
FEED_WEEKS = 4

# NUTS codes are 2-5 characters: a country, then up to three levels. 'EU' is
# the one non-NUTS value, meaning everywhere.
_MAX_REGION_LEN = 5
_MAX_REGIONS = 40

# Watches are cheap but not free: each one is a query against the items table
# every time the reading page loads. A ceiling stops a script turning one
# account into a load generator, and nobody legitimately follows 60 things.
MAX_WATCHES = 60


class BriefingService:
    def __init__(self, catalogue: NamedQueryRepository, feed: FeedRepository) -> None:
        self._catalogue = catalogue
        self._feed = feed

    # ── public catalogue ─────────────────────────────────────
    async def list_briefings(self) -> list[QueryGroup]:
        """Public briefings that actually have something published in them.

        A briefing with nothing published is omitted rather than shown empty:
        an empty shelf reads as a broken product, and there is nothing a
        visitor could do about it.
        """
        groups = await self._catalogue.list_groups(visibility="public")
        out = []
        for group in groups:
            group.queries = [q for q in group.queries if q.status == "published"]
            if group.queries:
                out.append(group)
        return out

    async def get_briefing(self, slug: str) -> QueryGroup:
        group = await self._catalogue.get_group_by_slug(slug)
        if group is None or group.visibility != "public":
            raise NotFound(f"Briefing '{slug}' not found")
        group.queries = [q for q in group.queries if q.status == "published"]
        return group

    async def preview(self, slug: str, nuts: list[str] | None = None,
                      volume: int = DEFAULT_VOLUME) -> list:
        """What this briefing looks like for these regions, without watching it.

        Reads the materialised table, so it costs a query against our own
        Postgres rather than an execution against the graph.
        """
        group = await self.get_briefing(slug)
        return await self._feed.rank_items(
            group.id, self._clean_regions(nuts), self._clean_volume(volume), FEED_WEEKS,
        )

    # ── watching ─────────────────────────────────────────────
    @staticmethod
    def _clean_regions(nuts: list[str] | None) -> list[str]:
        regions = [str(r).strip().upper() for r in (nuts or []) if str(r).strip()]
        if not regions or EVERYWHERE in regions:
            # Everywhere subsumes any other selection; keeping both would
            # imply a narrowing that does not happen.
            return [EVERYWHERE]
        if len(regions) > _MAX_REGIONS:
            raise InvalidInput(f"At most {_MAX_REGIONS} regions can be watched at once")
        for region in regions:
            if len(region) > _MAX_REGION_LEN or not region.isalnum():
                raise InvalidInput(f"'{region}' is not a NUTS region code")
        return sorted(set(regions))

    @staticmethod
    def _clean_volume(volume: int | None) -> int:
        value = int(volume if volume is not None else DEFAULT_VOLUME)
        if not MIN_VOLUME <= value <= MAX_VOLUME:
            raise InvalidInput(
                f"Choose between {MIN_VOLUME} and {MAX_VOLUME} items a week")
        return value

    async def watch(self, user_id: str, slug: str, nuts: list[str] | None = None,
                    volume: int | None = None) -> Watch:
        """Add a watch on a briefing.

        NOT idempotent by briefing, deliberately. A reader wanting fifty
        items a week from Coimbra, ten from Portugal and ten from the whole
        EU is asking for three watches on one briefing, and an earlier version
        of this collapsed them into one — silently overwriting the previous
        answer each time. Each watch is now an independent subscription with
        its own feed URL.

        The guard against genuine accidents is narrower: an exact duplicate —
        same briefing, same regions, same volume — returns the existing watch
        instead of minting a second identical feed, because that is a
        double-click, not an intention.
        """
        group = await self.get_briefing(slug)
        regions = self._clean_regions(nuts)
        per_week = self._clean_volume(volume)

        for existing in await self._feed.list_watches(user_id):
            if (existing.group_id == group.id
                    and existing.nuts == regions
                    and existing.volume_per_week == per_week):
                return existing

        if len(await self._feed.list_watches(user_id)) >= MAX_WATCHES:
            raise InvalidInput(
                f"You can hold {MAX_WATCHES} watches at once. Remove one first.")

        return await self._feed.create_watch(Watch(
            user_id=user_id, group_id=group.id, nuts=regions,
            volume_per_week=per_week,
            # 32 bytes of urlsafe randomness. It is a bearer secret in a URL
            # that will end up in reader logs, so it names the watch and
            # nothing else — no user id, no briefing slug.
            token=secrets.token_urlsafe(32),
        ))

    async def adjust_watch(self, user_id: str, watch_id: str,
                           nuts: list[str] | None = None,
                           volume: int | None = None) -> Watch:
        """Change one watch's regions or volume, by id.

        By id rather than by briefing, because "the watch on Public
        investment" is no longer a thing that identifies anything.
        """
        watch = await self._feed.get_watch(watch_id)
        if watch is None:
            raise NotFound(f"Watch {watch_id} not found")
        if watch.user_id != user_id:
            raise PermissionDenied("That watch belongs to someone else")
        if nuts is not None:
            watch.nuts = self._clean_regions(nuts)
        if volume is not None:
            watch.volume_per_week = self._clean_volume(volume)
        # The token is deliberately untouched: someone's reader is already
        # polling that URL, and changing the scope of a feed is not a reason
        # to break it.
        return await self._feed.update_watch(watch)

    async def list_watches(self, user_id: str) -> list[Watch]:
        return await self._feed.list_watches(user_id)

    async def unwatch(self, user_id: str, watch_id: str) -> None:
        watch = await self._feed.get_watch(watch_id)
        if watch is None:
            raise NotFound(f"Watch {watch_id} not found")
        if watch.user_id != user_id:
            raise PermissionDenied("That watch belongs to someone else")
        await self._feed.delete_watch(watch_id)

    # ── the feed itself ──────────────────────────────────────
    async def feed_for_token(self, token: str):
        """Resolve a feed URL to its briefing and items.

        The token is the only credential an Atom reader can carry. It grants
        exactly one thing — this watch's items — and the items are public
        records either way, so a leaked URL exposes a reading list, not data.
        """
        watch = await self._feed.get_watch_by_token(token)
        if watch is None:
            raise NotFound("Feed not found")
        group = await self._catalogue.get_group(watch.group_id)
        if group is None:
            raise NotFound("Feed not found")
        items = await self._feed.rank_items(
            watch.group_id, watch.nuts, watch.volume_per_week, FEED_WEEKS,
        )
        await self._feed.mark_polled(watch.id, datetime.now(timezone.utc))
        return watch, group, items
