"""Tag-related business rules.

Owns tag normalisation (free-text → slug) and the two caps the user
asked for (≤3 tags per story, ≤50 followed per user). The repo layer
treats tags as opaque strings; this service is the only place that
knows what a *valid* tag looks like.
"""
from __future__ import annotations

import re

from src.repositories.report_repository import ReportRepository
from src.repositories.tag_follow_repository import TagFollowRepository
from src.services.exceptions import InvalidInput, NotFound, PermissionDenied
from src.services.permission_service import PermissionService


# Tag = `[a-z0-9]+(-[a-z0-9]+)*`, max 40 chars. Mirrored in the
# alembic CHECK constraint so DB-level rejection lines up with the
# error the API surfaces.
_SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_MAX_LEN = 40
_NON_ALNUM = re.compile(r"[^a-z0-9]+")

MAX_TAGS_PER_STORY = 3
MAX_FOLLOWED_TAGS_PER_USER = 50


def normalise_tag(raw: str) -> str:
    """Free-text → slug.

    Pipeline: lowercase → replace any run of non-alphanumeric with a
    single hyphen → strip leading/trailing hyphens → cap at 40 chars.
    Empty results raise — the caller shouldn't have submitted whitespace.
    """
    if raw is None:
        raise InvalidInput("tag is required")
    cleaned = _NON_ALNUM.sub("-", raw.strip().lower()).strip("-")
    if not cleaned:
        raise InvalidInput(f"tag {raw!r} normalises to empty")
    if len(cleaned) > _MAX_LEN:
        cleaned = cleaned[:_MAX_LEN].rstrip("-")
    if not _SLUG_RE.match(cleaned):
        # Should be unreachable given the pipeline above, but a defensive
        # guard so a future regex change can't produce a garbage slug.
        raise InvalidInput(f"tag {raw!r} did not normalise to a valid slug")
    return cleaned


def normalise_tags(raw: list[str]) -> list[str]:
    """Apply ``normalise_tag`` to each, drop duplicates, preserve order."""
    seen: set[str] = set()
    out: list[str] = []
    for r in raw:
        slug = normalise_tag(r)
        if slug not in seen:
            seen.add(slug)
            out.append(slug)
    return out


class TagService:
    def __init__(
        self,
        reports: ReportRepository,
        follows: TagFollowRepository,
        perms: PermissionService,
    ) -> None:
        self._reports = reports
        self._follows = follows
        self._perms = perms

    # ── Story tags ────────────────────────────────────────────

    async def get_story_tags(self, report_id: str) -> list[str]:
        return await self._reports.get_story_tags(report_id)

    async def set_story_tags(
        self, user_id: str, report_id: str, tags: list[str],
    ) -> list[str]:
        """Replace the full tag set for a story. Owner-only.

        Returns the normalised tag set actually persisted (slugs,
        deduplicated, in input order).
        """
        report = await self._reports.get_by_id(report_id)
        if report is None:
            raise NotFound(f"story {report_id} not found")
        if report.created_by != user_id:
            raise PermissionDenied("only the author can edit tags")
        normalised = normalise_tags(tags)
        if len(normalised) > MAX_TAGS_PER_STORY:
            raise InvalidInput(
                f"at most {MAX_TAGS_PER_STORY} tags per story "
                f"(got {len(normalised)})",
            )
        await self._reports.set_story_tags(report_id, normalised)
        return normalised

    async def list_distinct_tags(self) -> list[tuple[str, int]]:
        return await self._reports.list_distinct_tags()

    # ── Followed tags ─────────────────────────────────────────

    async def list_followed(self, user_id: str) -> list[str]:
        return await self._follows.list(user_id)

    async def follow(self, user_id: str, tag: str) -> str:
        slug = normalise_tag(tag)
        # Cheap pre-check; the (user_id, tag) PK + ON CONFLICT in the
        # repo handles the racy case where two requests slip through.
        if slug in await self._follows.list(user_id):
            return slug
        if await self._follows.count(user_id) >= MAX_FOLLOWED_TAGS_PER_USER:
            raise InvalidInput(
                f"already following {MAX_FOLLOWED_TAGS_PER_USER} tags — "
                "unfollow one before adding a new one",
            )
        await self._follows.follow(user_id, slug)
        return slug

    async def unfollow(self, user_id: str, tag: str) -> str:
        slug = normalise_tag(tag)
        await self._follows.unfollow(user_id, slug)
        return slug
