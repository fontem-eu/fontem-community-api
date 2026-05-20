from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


# ── domain dataclass: each attribute is a separate column on the
#    users table. Splitting into sub-objects would just push the
#    flatten/unflatten cost into the repo layer.
@dataclass
class User:  # pylint: disable=too-many-instance-attributes
    id: str | None = None
    email: str = ""
    name: str = ""
    avatar_url: str | None = None
    password_hash: str | None = None  # bcrypt hash for local accounts, None for OAuth
    trust_level: str = "new_user"  # new_user, commenter, contributor, moderator, admin
    created_at: datetime | None = None
    failed_login_attempts: int = 0
    locked_until: datetime | None = None
