from __future__ import annotations

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

    async def update_own_profile(
        self, user_id: str, summary: str | None, links: list[dict] | None,
    ) -> dict:
        clean_summary = (summary or "").strip()[:MAX_SUMMARY]
        clean_links: list[ProfileLink] = []
        for raw in (links or [])[:MAX_LINKS]:
            name = (raw.get("name") or "").strip()[:MAX_LINK_NAME]
            url = (raw.get("url") or "").strip()[:MAX_LINK_URL]
            if not name or not url:
                continue
            if not url.startswith("https://"):
                continue
            clean_links.append(ProfileLink(name=name, url=url))
        saved = await self._profiles.upsert(
            UserProfile(user_id=user_id, summary=clean_summary, links=clean_links)
        )
        return {
            "summary": saved.summary,
            "links": [{"name": l.name, "url": l.url} for l in saved.links],
        }
