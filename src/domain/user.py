from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class User:
    id: str | None = None
    email: str = ""
    name: str = ""
    avatar_url: str | None = None
    trust_level: str = "new_user"  # new_user, commenter, contributor, moderator, admin
    created_at: datetime | None = None
