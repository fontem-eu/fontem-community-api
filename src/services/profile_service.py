from __future__ import annotations

import re

from src.domain.user_profile import ProfileLink, UserProfile
from src.repositories.activity_repository import ActivityRepository
from src.repositories.report_repository import ReportRepository
from src.repositories.user_profile_repository import UserProfileRepository
from src.repositories.user_repository import UserRepository
from src.services.exceptions import NotFound

MAX_LINKS = 10
MAX_SUMMARY = 2000
MAX_LINK_NAME = 60
MAX_LINK_URL = 500


_SCHEME_SLASHES = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.\-]*)://")
_BARE_SCHEME = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.\-]*):")


def _normalize_url(url: str) -> str | None:
    """Normalise a user-entered link URL so a valid link is never silently
    dropped just because the user omitted the scheme.

    - ``scheme://…`` — only http/https accepted; always normalised to https
      (avoids mixed content on the https app). Any other scheme rejected.
    - ``token:…`` without ``//`` (e.g. ``javascript:``, ``mailto:``) — rejected
      as an unsafe/non-web scheme, unless the token contains a dot (a bare
      ``host:port`` like ``example.com:8080``, which is a schemeless URL).
    - anything else — treated as schemeless and given an ``https://`` prefix.

    Returns None when the URL can't be made into a safe web link."""
    m = _SCHEME_SLASHES.match(url)
    if m:
        if m.group(1).lower() not in ("http", "https"):
            return None
        return "https://" + url[m.end():]
    b = _BARE_SCHEME.match(url)
    if b and "." not in b.group(1):
        return None
    return "https://" + url


class ProfileService:
    """Assembles a user's public profile: identity + editable extras
    (summary/links) + the public articles they authored + recent activity."""

    def __init__(
        self,
        users: UserRepository,
        profiles: UserProfileRepository,
        reports: ReportRepository,
        activity: ActivityRepository,
    ) -> None:
        self._users = users
        self._profiles = profiles
        self._reports = reports
        self._activity = activity

    async def get_profile(
        self, user_id: str, *, viewer_authed: bool,
        article_limit: int = 50, activity_limit: int = 20,
    ) -> dict:
        user = await self._users.get_by_id(user_id)
        if user is None:
            raise NotFound(f"User {user_id} not found")
        extras = await self._profiles.get(user_id)
        articles = await self._reports.list_public(
            article_limit, 0, authenticated=viewer_authed, author_id=user_id,
        )
        activity = await self._activity.list_for_actor(user_id, activity_limit, 0)
        return {
            "id": user.id,
            "name": user.name,
            "avatar_url": user.avatar_url,
            "trust_level": user.trust_level,
            "created_at": user.created_at,
            "summary": extras.summary if extras else "",
            "links": [{"name": l.name, "url": l.url}
                      for l in (extras.links if extras else [])],
            "avatar_x": extras.avatar_x if extras else 50.0,
            "avatar_y": extras.avatar_y if extras else 50.0,
            "articles": [
                {
                    "id": a.id,
                    "title": a.title,
                    "abstract": getattr(a, "abstract", "") or "",
                    "visibility": a.visibility,
                    "created_at": getattr(a, "created_at", None),
                }
                for a in articles
            ],
            "recent_activity": [
                {
                    "entity_type": e.entity_type,
                    "entity_id": e.entity_id,
                    "action": e.action,
                    "summary": e.summary,
                    "created_at": e.created_at,
                }
                for e in activity
            ],
        }

    async def update_own_profile(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self, user_id: str, summary: str | None, links: list[dict] | None,
        avatar_x: float | None = None, avatar_y: float | None = None,
    ) -> dict:
        clean_summary = (summary or "").strip()[:MAX_SUMMARY]
        clean_links: list[ProfileLink] = []
        for raw in (links or [])[:MAX_LINKS]:
            name = (raw.get("name") or "").strip()[:MAX_LINK_NAME]
            url = (raw.get("url") or "").strip()[:MAX_LINK_URL]
            if not name or not url:
                continue
            normalised = _normalize_url(url)
            if normalised is None:
                continue
            clean_links.append(ProfileLink(name=name, url=normalised[:MAX_LINK_URL]))
        # Preserve the stored focal point unless the caller sends a new one.
        existing = await self._profiles.get(user_id)
        ax = existing.avatar_x if existing else 50.0
        ay = existing.avatar_y if existing else 50.0
        if avatar_x is not None:
            ax = min(100.0, max(0.0, float(avatar_x)))
        if avatar_y is not None:
            ay = min(100.0, max(0.0, float(avatar_y)))
        saved = await self._profiles.upsert(UserProfile(
            user_id=user_id, summary=clean_summary, links=clean_links,
            avatar_x=ax, avatar_y=ay,
        ))
        return {
            "summary": saved.summary,
            "links": [{"name": l.name, "url": l.url} for l in saved.links],
            "avatar_x": saved.avatar_x,
            "avatar_y": saved.avatar_y,
        }
