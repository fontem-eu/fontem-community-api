from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class Group:
    id: str | None = None
    name: str = ""
    description: str = ""
    created_at: datetime | None = None
