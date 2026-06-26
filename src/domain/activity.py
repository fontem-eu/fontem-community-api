from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

# Entity types that produce activity, and the CUD verbs recorded.
ENTITY_TYPES = ("story", "dossier", "investigation", "issue")
ACTIONS = ("created", "updated", "deleted")


@dataclass
class ActivityEvent:
    """One create/update/delete a user performed, for their activity feed."""
    id: str | None = None
    actor_id: str = ""        # the user who performed the action
    entity_type: str = ""     # story | dossier | investigation | issue
    entity_id: str = ""
    action: str = ""          # created | updated | deleted
    summary: str = ""         # title/name captured at event time (for display)
    created_at: datetime | None = None
