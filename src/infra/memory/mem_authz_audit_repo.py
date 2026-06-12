"""In-memory AuthzAuditRepository for unit tests.

Keeps every recorded row on the instance so tests can assert "this
decision was logged with this reason". Production uses
:class:`PgAuthzAuditRepository`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from src.services.authz.audit import AuthzAuditRepository


@dataclass
class _Row:
    timestamp: datetime
    user_id: str | None
    action: str
    resource_kind: str | None
    resource_id: str | None
    allowed: bool
    reason: str


class InMemoryAuthzAuditRepository(AuthzAuditRepository):
    def __init__(self) -> None:
        self.rows: list[_Row] = []

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
        self.rows.append(_Row(
            timestamp=timestamp,
            user_id=user_id,
            action=action,
            resource_kind=resource_kind,
            resource_id=resource_id,
            allowed=allowed,
            reason=reason,
        ))
