from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ProfileLink:
    """One labelled link in a user's profile (``name`` -> ``url``)."""
    name: str = ""
    url: str = ""


@dataclass
class UserProfile:  # pylint: disable=too-many-instance-attributes
    """Editable public-profile extras for a user.

    Kept in a side table (``user_profiles``) rather than on ``users`` so it
    ships via ``create_all`` (a new table) instead of needing a manual ALTER
    on the users table in prod.
    """
    user_id: str = ""
    summary: str = ""
    links: list[ProfileLink] = field(default_factory=list)
    # Avatar focal point as percentages (CSS object-position) so the user can
    # centre their photo within the round frame. Default 50/50 = centred.
    avatar_x: float = 50.0
    avatar_y: float = 50.0
    # Email display: opt-in (show_email); optionally a public-facing address
    # different from the account email (use_custom_email + custom_email).
    show_email: bool = False
    use_custom_email: bool = False
    custom_email: str = ""
    updated_at: datetime | None = None
