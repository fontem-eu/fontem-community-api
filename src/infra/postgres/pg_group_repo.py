from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.group import Group
from src.infra.postgres.models import GroupMemberModel, GroupModel
from src.repositories.group_repository import GroupRepository


class PgGroupRepository(GroupRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _to_domain(row: GroupModel) -> Group:
        return Group(
            id=row.id,
            name=row.name,
            description=row.description,
            created_by=row.created_by,
            created_at=row.created_at,
        )

    async def create(self, group: Group) -> Group:
        now = datetime.now(timezone.utc)
        model = GroupModel(
            id=group.id or str(uuid4()),
            name=group.name,
            description=group.description,
            created_by=group.created_by,
            created_at=group.created_at or now,
        )
        self._session.add(model)
        await self._session.commit()
        return self._to_domain(model)

    async def get_by_id(self, group_id: str) -> Group | None:
        result = await self._session.execute(
            select(GroupModel).where(GroupModel.id == group_id)
        )
        row = result.scalar_one_or_none()
        return self._to_domain(row) if row else None

    async def add_member(self, group_id: str, user_id: str) -> None:
        self._session.add(GroupMemberModel(group_id=group_id, user_id=user_id))
        await self._session.commit()

    async def remove_member(self, group_id: str, user_id: str) -> None:
        await self._session.execute(
            delete(GroupMemberModel)
            .where(GroupMemberModel.group_id == group_id)
            .where(GroupMemberModel.user_id == user_id)
        )
        await self._session.commit()

    async def get_members(self, group_id: str) -> list[str]:
        result = await self._session.execute(
            select(GroupMemberModel.user_id).where(
                GroupMemberModel.group_id == group_id
            )
        )
        return list(result.scalars().all())

    async def get_user_groups(self, user_id: str) -> list[Group]:
        result = await self._session.execute(
            select(GroupModel)
            .join(GroupMemberModel, GroupMemberModel.group_id == GroupModel.id)
            .where(GroupMemberModel.user_id == user_id)
        )
        return [self._to_domain(r) for r in result.scalars().all()]
