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
MAX_NAME = 100
MAX_EMAIL = 254


def _valid_email(email: str) -> bool:
    """Lightweight email sanity check (no regex backtracking): exactly one
    '@', non-empty local part, a domain with a dot, and no whitespace or
    control characters (so a value can't smuggle a CRLF into the `mailto:`
    link the profile renders)."""
    email = email.strip()
    if (not email or len(email) > MAX_EMAIL or email.count("@") != 1
            or any(c.isspace() or ord(c) < 0x20 or ord(c) == 0x7f for c in email)):
        return False
    local, _, domain = email.partition("@")
    return bool(local) and bool(domain) and "." in domain and not domain.startswith(".")


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


def _clean_links(links: list[dict] | None) -> list[ProfileLink]:
    out: list[ProfileLink] = []
    for raw in (links or [])[:MAX_LINKS]:
        lname = (raw.get("name") or "").strip()[:MAX_LINK_NAME]
        url = (raw.get("url") or "").strip()[:MAX_LINK_URL]
        if not lname or not url:
            continue
        normalised = _normalize_url(url)
        if normalised is None:
            continue
        out.append(ProfileLink(name=lname, url=normalised[:MAX_LINK_URL]))
    return out


_NUTS_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{0,3}$")


def _merge_home_nuts(existing, home_nuts):
    """Resolve the stored home NUTS code against an update.

    ``None`` leaves it untouched (partial update); an empty string clears it;
    a valid NUTS code (2-letter country + up to 3 alnum, any level) is stored
    normalised to upper-case. Anything malformed is ignored (keeps existing)
    so a bad client value can't wipe a good one.
    """
    current = existing.home_nuts if existing else ""
    if home_nuts is None:
        return current
    code = home_nuts.strip().upper()
    if code == "":
        return ""
    return code if _NUTS_RE.match(code) else current


def _merge_email(existing, show_email, use_custom_email, custom_email):
    show = existing.show_email if existing else False
    use_custom = existing.use_custom_email if existing else False
    custom = existing.custom_email if existing else ""
    if show_email is not None:
        show = bool(show_email)
    if use_custom_email is not None:
        use_custom = bool(use_custom_email)
    if custom_email is not None:
        ce = custom_email.strip()[:MAX_EMAIL]
        custom = ce if _valid_email(ce) else ""
    if use_custom and not _valid_email(custom):
        use_custom = False
    return show, use_custom, custom



def _public_email(user, show_email: bool, use_custom: bool, custom: str) -> str:
    """The address shown publicly: nothing unless opted in; the custom address
    when set, else the account email."""
    if not show_email:
        return ""
    return custom if (use_custom and custom) else user.email


def _visible_activity(activity, articles, *, is_owner: bool):
    """A user's activity is their own. Nobody else sees any of it.

    This used to show non-owners the subset that touched the author's PUBLIC
    stories, on the reasoning that a public artefact's history is public. The
    reasoning is defensible and the filter worked, but it made every new kind
    of activity a disclosure decision by default: the log has since grown to
    record Data Studio work, and now carries which actions an AGENT took on
    someone's behalf, in which conversation. None of that was considered when
    the filter was written, and all of it would have been published by it.

    Closed by default instead. Opening it back up for public artefacts is a
    deliberate act — it needs a per-entry visibility rule rather than an
    entity_type check, because "activity on a public story" and "activity
    that is safe to publish" are not the same set.

    `articles` is kept in the signature: the caller has it, and the rule it
    feeds is exactly what a future selective opening would use.
    """
    del articles
    return activity if is_owner else []


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

    async def get_profile(  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
        self, user_id: str, *, viewer_authed: bool, caller_id: str | None = None,
        article_limit: int = 50, activity_limit: int = 20,
    ) -> dict:
        user = await self._users.get_by_id(user_id)
        if user is None:
            raise NotFound(f"User {user_id} not found")
        extras = await self._profiles.get(user_id)
        show_email = extras.show_email if extras else False
        use_custom = extras.use_custom_email if extras else False
        custom = extras.custom_email if extras else ""
        public_email = _public_email(user, show_email, use_custom, custom)
        is_owner = caller_id is not None and caller_id == user_id
        articles = await self._reports.list_public(
            article_limit, 0, authenticated=viewer_authed, author_id=user_id,
        )
        activity = await self._activity.list_for_actor(user_id, activity_limit, 0)
        visible_activity = _visible_activity(activity, articles, is_owner=is_owner)
        result = {
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
            "home_nuts": extras.home_nuts if extras else "",
            "email": public_email,
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
                for e in visible_activity
            ],
        }
        # Owner viewing their own profile: expose the editable email settings.
        if is_owner:
            result["show_email"] = show_email
            result["use_custom_email"] = use_custom
            result["custom_email"] = custom
            result["account_email"] = user.email
        return result

    async def _maybe_rename(self, user_id: str, name: str | None) -> str | None:
        """Rename the user (name lives on the user record) when a non-empty
        name is supplied; returns the current name either way."""
        user = await self._users.get_by_id(user_id)
        if user is None:
            return None
        if name is not None:
            nm = name.strip()[:MAX_NAME]
            if nm and nm != user.name:
                user.name = nm
                await self._users.upsert(user)
                return nm
        return user.name

    async def update_own_profile(  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
        self, user_id: str, summary: str | None, links: list[dict] | None,
        avatar_x: float | None = None, avatar_y: float | None = None,
        name: str | None = None, show_email: bool | None = None,
        use_custom_email: bool | None = None, custom_email: str | None = None,
        home_nuts: str | None = None,
    ) -> dict:
        clean_summary = (summary or "").strip()[:MAX_SUMMARY]
        clean_links = _clean_links(links)
        # Merge focal point + email settings with what's stored, so a partial
        # update (e.g. summary only) never wipes them.
        existing = await self._profiles.get(user_id)
        ax = existing.avatar_x if existing else 50.0
        ay = existing.avatar_y if existing else 50.0
        if avatar_x is not None:
            ax = min(100.0, max(0.0, float(avatar_x)))
        if avatar_y is not None:
            ay = min(100.0, max(0.0, float(avatar_y)))
        show, use_custom, custom = _merge_email(
            existing, show_email, use_custom_email, custom_email)
        home = _merge_home_nuts(existing, home_nuts)
        saved = await self._profiles.upsert(UserProfile(
            user_id=user_id, summary=clean_summary, links=clean_links,
            avatar_x=ax, avatar_y=ay, show_email=show,
            use_custom_email=use_custom, custom_email=custom, home_nuts=home,
        ))
        current_name = await self._maybe_rename(user_id, name)
        return {
            "name": current_name,
            "summary": saved.summary,
            "links": [{"name": l.name, "url": l.url} for l in saved.links],
            "avatar_x": saved.avatar_x,
            "avatar_y": saved.avatar_y,
            "show_email": saved.show_email,
            "use_custom_email": saved.use_custom_email,
            "custom_email": saved.custom_email,
            "home_nuts": saved.home_nuts,
        }
