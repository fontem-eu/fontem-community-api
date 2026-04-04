from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class Flag:
    id: str | None = None
    target_type: str = ""  # report, comment, issue
    target_id: str = ""
    reason: str = "other"  # inaccurate, spam, harassment, off_topic, other
    details: str = ""
    flagged_by: str = ""
    created_at: datetime | None = None


@dataclass
class Sanction:
    id: str | None = None
    user_id: str = ""
    type: str = "warning"  # warning, mute, suspend, ban
    reason: str = ""
    starts_at: datetime | None = None
    expires_at: datetime | None = None
    applied_by: str = ""
    lifted_at: datetime | None = None
