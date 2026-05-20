# ``sqlalchemy.func.count`` is a magic factory pylint can't introspect;
# every call lights up E1102 as a false positive. See pg_report_repo.py.
# pylint: disable=not-callable
from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.infra.postgres.models import UserFollowedTagModel
from src.repositories.tag_follow_repository import TagFollowRepository


class PgTagFollowRepository(TagFollowRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list(self, user_id: str) -> list[str]:
        stmt = (
            select(UserFollowedTagModel.tag)
            .where(UserFollowedTagModel.user_id == user_id)
            .order_by(UserFollowedTagModel.tag)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def follow(self, user_id: str, tag: str) -> None:
        # ON CONFLICT DO NOTHING — the (user_id, tag) PK makes this
        # idempotent and safe under the (rare) concurrent-tap-in-two-
        # tabs race.
        await self._session.execute(
            pg_insert(UserFollowedTagModel)
            .values(user_id=user_id, tag=tag)
            .on_conflict_do_nothing(index_elements=["user_id", "tag"])
        )
        await self._session.commit()

    async def unfollow(self, user_id: str, tag: str) -> None:
        await self._session.execute(
            delete(UserFollowedTagModel).where(
                (UserFollowedTagModel.user_id == user_id)
                & (UserFollowedTagModel.tag == tag)
            )
        )
        await self._session.commit()

    async def count(self, user_id: str) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(UserFollowedTagModel)
            .where(UserFollowedTagModel.user_id == user_id)
        )
        return int(result.scalar_one())
