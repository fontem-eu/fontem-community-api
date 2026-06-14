"""Postgres implementation of :class:`AuthTokenRepository`."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from src.infra.postgres.models import AuthTokenModel, _utcnow
from src.repositories.auth_token_repository import AuthToken, AuthTokenRepository


def _to_domain(m: AuthTokenModel) -> AuthToken:
    return AuthToken(
        id=m.id,
        user_id=m.user_id,
        token_hash=m.token_hash,
        purpose=m.purpose,
        expires_at=m.expires_at,
        consumed_at=m.consumed_at,
        created_at=m.created_at,
    )


class PgAuthTokenRepository(AuthTokenRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, token: AuthToken) -> AuthToken:
        m = AuthTokenModel(
            id=token.id,
            user_id=token.user_id,
            token_hash=token.token_hash,
            purpose=token.purpose,
            expires_at=token.expires_at,
            consumed_at=token.consumed_at,
        )
        self._session.add(m)
        await self._session.flush()
        await self._session.commit()
        return _to_domain(m)

    async def consume(self, token_hash: str, purpose: str, now: datetime) -> AuthToken | None:
        # Atomic: only an unconsumed, unexpired, matching row updates.
        # RETURNING gives us the row we just claimed; a concurrent
        # second redeem finds 0 rows because consumed_at is now set.
        stmt = (
            update(AuthTokenModel)
            .where(
                AuthTokenModel.token_hash == token_hash,
                AuthTokenModel.purpose == purpose,
                AuthTokenModel.consumed_at.is_(None),
                AuthTokenModel.expires_at > now,
            )
            .values(consumed_at=now)
            .returning(AuthTokenModel)
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        await self._session.commit()
        return _to_domain(row) if row is not None else None

    async def invalidate_outstanding(self, user_id: str, purpose: str) -> int:
        stmt = (
            update(AuthTokenModel)
            .where(
                AuthTokenModel.user_id == user_id,
                AuthTokenModel.purpose == purpose,
                AuthTokenModel.consumed_at.is_(None),
            )
            .values(consumed_at=_utcnow())
        )
        result = await self._session.execute(stmt)
        await self._session.commit()
        return result.rowcount or 0
