from __future__ import annotations

import logging

from src.domain.activity import ActivityEvent
from src.repositories.activity_repository import ActivityRepository

logger = logging.getLogger(__name__)


class ActivityService:
    """Records and reads a user's create/update/delete activity."""

    def __init__(self, activity_repo: ActivityRepository) -> None:
        self._repo = activity_repo

    async def record(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self, actor_id: str, entity_type: str, entity_id: str, action: str, summary: str = ""
    ) -> None:
        """Record a CUD event. Best-effort: a write failure must never break the
        underlying operation (mirrors the authz AuditLogger)."""
        try:
            await self._repo.record(
                ActivityEvent(
                    actor_id=actor_id,
                    entity_type=entity_type,
                    entity_id=entity_id or "",
                    action=action,
                    summary=summary or "",
                )
            )
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception(
                "activity record failed actor=%s %s %s %s",
                actor_id, action, entity_type, entity_id,
            )

    async def list_for_actor(self, actor_id: str, limit: int = 50, offset: int = 0) -> list[dict]:
        events = await self._repo.list_for_actor(actor_id, limit, offset)
        return [
            {
                "id": e.id,
                "entity_type": e.entity_type,
                "entity_id": e.entity_id,
                "action": e.action,
                "summary": e.summary,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in events
        ]
