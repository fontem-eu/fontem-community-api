from __future__ import annotations

import logging

from src.domain.activity import ActivityEvent
from src.services import audit_context
from src.repositories.activity_repository import ActivityRepository

logger = logging.getLogger(__name__)


class ActivityService:
    """Records and reads a user's create/update/delete activity."""

    def __init__(self, activity_repo: ActivityRepository) -> None:
        self._repo = activity_repo

    async def record(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self, actor_id: str, entity_type: str, entity_id: str, action: str,
        summary: str = "",
    ) -> None:
        """Record a CUD event, with the provenance of whoever is acting.

        Provenance comes from the ambient AuditContext rather than the
        argument list: the caller usually does not know whether it is being
        driven by a person or by the assistant, and threading four more
        parameters through every service signature to tell it would guarantee
        the ones nobody remembered to update.

        Best-effort on the WRITE only, and deliberately still so: an audit
        failure must not roll back the thing being audited. It is not
        silent — the exception is logged with everything needed to
        reconstruct the lost entry.

        An action an agent may not perform raises instead, before anything
        is written: refusing is the point, and swallowing it would let the
        action proceed unrecorded, which is the worst of both.
        """
        ctx = audit_context.current()
        ctx.check(action)
        ctx.note(entity_type, action)
        try:
            await self._repo.record(
                ActivityEvent(
                    actor_id=actor_id,
                    entity_type=entity_type,
                    entity_id=entity_id or "",
                    action=action,
                    summary=summary or "",
                    actor_kind=ctx.actor_kind,
                    conversation_id=ctx.conversation_id,
                    message_id=ctx.message_id,
                    request_id=ctx.request_id,
                )
            )
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception(
                "activity record failed actor=%s kind=%s %s %s %s conv=%s call=%s",
                actor_id, ctx.actor_kind, action, entity_type, entity_id,
                ctx.conversation_id, ctx.message_id,
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
                # What the feed needs to say "the assistant did this" and
                # link back to the exact call that did it.
                "actor_kind": e.actor_kind,
                "conversation_id": e.conversation_id,
                "message_id": e.message_id,
            }
            for e in events
        ]
