"""Audit logger for AuthorizationService decisions.

Every ``decide`` call hits this — both allow and deny — and the row
lands in the ``authz_audit`` Postgres table. Async + fire-and-forget
semantics so the hot path doesn't pay for the database write; we
catch the audit table going down rather than failing the request.

Why log allow-decisions too: investigating "how did this user end up
with that data" is a million times faster when you have a complete
record of every authorization decision they hit, not just the
denials. Storage cost is trivial (a few hundred bytes per row, low
write volume on a transparency platform).
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from uuid import uuid4

from src.services.authz.actions import Action
from src.services.authz.policy import Decision, Principal, ResourceRef

logger = logging.getLogger(__name__)


class AuthzAuditRepository(ABC):
    """Repository for ``authz_audit`` rows.

    Lives next to the policy decision so a test fake can be slotted
    in without touching the real DB. Production uses
    :class:`PgAuthzAuditRepository`; the in-memory ``services``
    fixture for unit tests uses an in-memory shim.
    """

    @abstractmethod
    async def record(
        self,
        *,
        timestamp: datetime,
        user_id: str | None,
        action: str,
        resource_kind: str | None,
        resource_id: str | None,
        allowed: bool,
        reason: str,
    ) -> None: ...


class AuditLogger:
    """Thin wrapper around the repo that knows the AuthorizationService
    inputs."""

    def __init__(self, repo: AuthzAuditRepository) -> None:
        self._repo = repo

    async def record(
        self,
        principal: Principal | None,
        action: Action,
        resource: ResourceRef | None,
        decision: Decision,
    ) -> None:
        try:
            await self._repo.record(
                timestamp=datetime.now(timezone.utc),
                user_id=principal.user_id if principal else None,
                action=action.value,
                resource_kind=resource.kind if resource else None,
                resource_id=resource.id if resource else None,
                allowed=decision.allowed,
                reason=decision.reason,
            )
        except Exception:  # pylint: disable=broad-exception-caught
            # An audit-table outage must not take down the platform —
            # log + swallow. The structured log line includes enough
            # detail to recover the audit row from the central log
            # aggregation if the audit table itself is unavailable.
            logger.exception(
                "authz_audit write failed user=%s action=%s "
                "resource=%s:%s allowed=%s reason=%s",
                principal.user_id if principal else None,
                action.value,
                resource.kind if resource else None,
                resource.id if resource else None,
                decision.allowed,
                decision.reason,
            )


__all__ = ["AuthzAuditRepository", "AuditLogger"]
