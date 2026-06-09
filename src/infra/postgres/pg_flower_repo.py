"""Postgres implementation of the FlowerRepository.

The ``increment`` path uses a single ``INSERT … ON CONFLICT DO
UPDATE … RETURNING count`` so a click is one round-trip and free of
the read-modify-write race that a separate SELECT + UPDATE would
introduce when the user clicks twice from two tabs in quick
succession.
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.infra.postgres.models import FlowerGivenModel
from src.repositories.flower_repository import FlowerRepository


class PgFlowerRepository(FlowerRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_mine(self, user_id: str, report_id: str) -> int:
        stmt = (
            select(FlowerGivenModel.count)
            .where(
                FlowerGivenModel.user_id == user_id,
                FlowerGivenModel.report_id == report_id,
            )
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        return int(row) if row is not None else 0

    async def get_total(self, report_id: str) -> int:
        stmt = (
            select(func.coalesce(func.sum(FlowerGivenModel.count), 0))
            .where(FlowerGivenModel.report_id == report_id)
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one())

    async def increment(
        self, user_id: str, report_id: str, *, cap: int,
    ) -> int | None:
        # Atomic upsert with the cap enforced in the WHERE clause of
        # the DO UPDATE: on first click insert with count=1; on later
        # clicks bump by 1 IFF the existing count is still < cap.
        # RETURNING gives us the post-write value in the same
        # statement, so there's no read-then-write race. When the
        # WHERE blocks the update (existing count == cap), the
        # statement returns zero rows and we surface that as None so
        # the service can raise InvalidInput.
        stmt = (
            pg_insert(FlowerGivenModel)
            .values(user_id=user_id, report_id=report_id, count=1)
            .on_conflict_do_update(
                index_elements=["user_id", "report_id"],
                set_={
                    "count": FlowerGivenModel.__table__.c.count + 1,
                    "updated_at": func.now(),
                },
                where=FlowerGivenModel.__table__.c.count < cap,
            )
            .returning(FlowerGivenModel.count)
        )
        result = await self._session.execute(stmt)
        await self._session.commit()
        row = result.scalar_one_or_none()
        return int(row) if row is not None else None
