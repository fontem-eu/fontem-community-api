from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ProfileLink:
    """One labelled link in a user's profile (``name`` -> ``url``)."""
    name: str = ""
    url: str = ""


@dataclass
class UserProfile:
    """Editable public-profile extras for a user.

    Kept in a side table (``user_profiles``) rather than on ``users`` so it
    ships via ``create_all`` (a new table) instead of needing a manual ALTER
    on the users table in prod.
    """
    user_id: str = ""
    summary: str = ""
    links: list[ProfileLink] = field(default_factory=list)
    updated_at: datetime | None = None
