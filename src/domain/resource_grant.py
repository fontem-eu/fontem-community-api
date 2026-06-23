from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class ResourceGrant:
    """A direct per-item access grant (the additive override). Generic over
    dossiers and viz (``resource_type``). Levels mirror report_access:
    viewer < commenter < editor < owner.
    """

    resource_type: str
    resource_id: str
    user_id: str
    level: str = "viewer"
    created_at: datetime | None = None
