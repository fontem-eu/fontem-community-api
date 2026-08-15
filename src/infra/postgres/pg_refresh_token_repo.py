"""Postgres implementation of :class:`RefreshTokenRepository`.

Rotation is the hot path. The implementation uses an UPDATE with a
WHERE clause matching the old ``current_token_hash`` *and*
``revoked_at IS NULL`` so two concurrent refreshes from the same
family produce exactly one ``True`` — the loser sees ``False`` (zero
rows affected) and the service treats that as reuse-signal.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.infra.postgres.models import RefreshTokenFamilyModel, _utcnow
from src.repositories.refresh_token_repository import (
    RefreshTokenFamily,
    RefreshTokenRepository,
)


def _to_domain(m: RefreshTokenFamilyModel) -> RefreshTokenFamily:
    return RefreshTokenFamily(
        id=m.id,
        user_id=m.user_id,
        current_token_hash=m.current_token_hash,
        previous_token_hash=m.previous_token_hash,
        rotated_at=m.rotated_at,
        expires_at=m.expires_at,
        revoked_at=m.revoked_at,
        revoked_reason=m.revoked_reason,
        created_user_agent_hash=m.created_user_agent_hash,
        created_ip_hash=m.created_ip_hash,
    )


class PgRefreshTokenRepository(RefreshTokenRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_family(
        self, family: RefreshTokenFamily,
    ) -> RefreshTokenFamily:
        m = RefreshTokenFamilyModel(
            id=family.id,
            user_id=family.user_id,
            current_token_hash=family.current_token_hash,
            rotated_at=family.rotated_at,
            expires_at=family.expires_at,
            revoked_at=family.revoked_at,
            revoked_reason=family.revoked_reason,
            created_user_agent_hash=family.created_user_agent_hash,
            created_ip_hash=family.created_ip_hash,
        )
        self._session.add(m)
        await self._session.flush()
        return _to_domain(m)

    async def find_by_current_hash(
        self, token_hash: str,
    ) -> RefreshTokenFamily | None:
        stmt = select(RefreshTokenFamilyModel).where(
            RefreshTokenFamilyModel.current_token_hash == token_hash,
        )
        result = await self._session.execute(stmt)
        m = result.scalar_one_or_none()
        return _to_domain(m) if m is not None else None

    async def find_by_previous_hash(
        self, token_hash: str,
    ) -> RefreshTokenFamily | None:
        stmt = select(RefreshTokenFamilyModel).where(
            RefreshTokenFamilyModel.previous_token_hash == token_hash,
        )
        result = await self._session.execute(stmt)
        m = result.scalar_one_or_none()
        return _to_domain(m) if m is not None else None

    async def rotate(
        self,
        family_id: str,
        new_token_hash: str,
        new_expires_at: datetime,
    ) -> bool:
        """Single UPDATE; returns True iff exactly one row changed.

        The WHERE clause requires ``revoked_at IS NULL`` so a revoked
        family can never silently come back. Two concurrent rotations
        racing each other compete on the row lock; only the first
        succeeds, the second's WHERE no longer matches and 0 rows
        update.
        """
        stmt = (
            update(RefreshTokenFamilyModel)
            .where(
                RefreshTokenFamilyModel.id == family_id,
                RefreshTokenFamilyModel.revoked_at.is_(None),
            )
            .values(
                # Keep what we are replacing: it is what a second tab will
                # offer a moment from now, and what a thief would replay
                # much later. `rotated_at` is how those are told apart.
                previous_token_hash=RefreshTokenFamilyModel.current_token_hash,
                current_token_hash=new_token_hash,
                rotated_at=_utcnow(),
                expires_at=new_expires_at,
            )
        )
        result = await self._session.execute(stmt)
        return result.rowcount == 1

    async def revoke_family(self, family_id: str, reason: str) -> None:
        stmt = (
            update(RefreshTokenFamilyModel)
            .where(RefreshTokenFamilyModel.id == family_id)
            .values(revoked_at=_utcnow(), revoked_reason=reason)
        )
        await self._session.execute(stmt)

    async def revoke_all_for_user(self, user_id: str, reason: str) -> int:
        stmt = (
            update(RefreshTokenFamilyModel)
            .where(
                RefreshTokenFamilyModel.user_id == user_id,
                RefreshTokenFamilyModel.revoked_at.is_(None),
            )
            .values(revoked_at=_utcnow(), revoked_reason=reason)
        )
        result = await self._session.execute(stmt)
        return result.rowcount or 0

    async def get_by_id(self, family_id: str) -> RefreshTokenFamily | None:
        m = await self._session.get(RefreshTokenFamilyModel, family_id)
        return _to_domain(m) if m is not None else None
