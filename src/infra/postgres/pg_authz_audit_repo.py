"""Postgres-backed AuthzAuditRepository.

Appends one row per decision. Commits eagerly because the audit
record needs to survive whatever happens to the request's main
transaction — if the route handler crashes after the authz check,
we still want the row recorded.
"""
from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from src.infra.postgres.models import AuthzAuditModel
from src.services.authz.audit import AuthzAuditRepository


class PgAuthzAuditRepository(AuthzAuditRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

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
    ) -> None:
        row = AuthzAuditModel(
            id=str(uuid4()),
            timestamp=timestamp,
            user_id=user_id,
            action=action,
            resource_kind=resource_kind,
            resource_id=resource_id,
            allowed=allowed,
            reason=reason,
        )
        self._session.add(row)
        await self._session.commit()
