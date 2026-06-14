"""AuthorizationService — the thing routers inject.

Routers call ``await authz.require(principal, action, resource)``.
The service:

1. Runs the policy via :func:`policy.evaluate`.
2. Records the decision via :class:`AuditLogger`.
3. Raises :class:`PermissionDenied` if the verdict is deny.

It also provides ``principal(user_id)`` to build the
:class:`Principal` snapshot. The snapshot bundles the three pieces
the policy needs (trust level, roles, active sanction) so a single
``require`` call is one read from each of those, not one per check.

In practice a request usually makes 1–2 ``require`` calls. The
``principal`` snapshot is cheap to keep cached for the request's
lifetime; we don't bother for now because every handler is
short-lived and the reads are direct PK lookups.
"""
from __future__ import annotations

from src.repositories.user_repository import UserRepository
from src.services.authz.actions import Action
from src.services.authz.audit import AuditLogger
from src.services.authz.policy import (
    Decision,
    Principal,
    ResourceRef,
    evaluate,
)
from src.services.exceptions import PermissionDenied


class AuthorizationService:
    """Single decision point. Inject into every router that mutates state."""

    def __init__(self, users: UserRepository, audit: AuditLogger) -> None:
        self._users = users
        self._audit = audit

    async def principal(self, user_id: str | None) -> Principal | None:
        """Build a Principal snapshot for ``user_id``.

        Returns None when ``user_id`` is None (unauthenticated). The
        public-read actions in the policy table are configured to
        allow None principals; everything else denies before even
        looking at the resource.
        """
        if user_id is None:
            return None
        user = await self._users.get_by_id(user_id)
        if user is None:
            # The caller authenticated successfully (JWT validated) but
            # the row vanished — surface as an unauthenticated principal
            # so the action gets denied by the policy.
            return None
        roles = frozenset(await self._users.get_roles(user_id))
        sanction = await self._users.get_active_sanction(user_id)
        sanction_type = sanction.type if sanction is not None else None
        return Principal(
            user_id=user_id,
            trust_level=user.trust_level,
            roles=roles,
            sanction=sanction_type,
            email_verified=user.email_verified_at is not None,
        )

    async def decide(
        self,
        principal: Principal | None,
        action: Action,
        resource: ResourceRef | None = None,
    ) -> Decision:
        """Compute and audit the decision; do NOT raise.

        Use this for "should I show this UI?" checks where you want
        the verdict but not an exception. The audit row records that
        the check happened.
        """
        if principal is None:
            verdict = Decision.deny("unauthenticated")
        else:
            verdict = evaluate(principal, action, resource)
        await self._audit.record(principal, action, resource, verdict)
        return verdict

    async def require(
        self,
        principal: Principal | None,
        action: Action,
        resource: ResourceRef | None = None,
    ) -> None:
        """Compute, audit, and raise :class:`PermissionDenied` on deny.

        The standard path for every state-mutating router handler.
        """
        verdict = await self.decide(principal, action, resource)
        if not verdict.allowed:
            raise PermissionDenied(verdict.reason)


__all__ = ["AuthorizationService"]
