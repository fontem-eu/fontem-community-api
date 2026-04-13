from __future__ import annotations

from uuid import uuid4

from sqlalchemy import delete, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.report import AccessGrant
from src.infra.postgres.models import GroupMemberModel, ReportAccessModel, ReportModel
from src.repositories.permission_repository import PermissionRepository

LEVEL_HIERARCHY: dict[str, int] = {
    "viewer": 0,
    "commenter": 1,
    "editor": 2,
    "owner": 3,
}


class PgPermissionRepository(PermissionRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _to_domain(row: ReportAccessModel) -> AccessGrant:
        return AccessGrant(
            id=row.id,
            report_id=row.report_id,
            user_id=row.user_id,
            group_id=row.group_id,
            level=row.level,
        )

    async def get_report_access(self, user_id: str, report_id: str) -> str | None:
        # Direct user access
        direct_q = (
            select(ReportAccessModel.level)
            .where(ReportAccessModel.report_id == report_id)
            .where(ReportAccessModel.user_id == user_id)
        )

        # Group access via group_members
        group_q = (
            select(ReportAccessModel.level)
            .join(
                GroupMemberModel,
                GroupMemberModel.group_id == ReportAccessModel.group_id,
            )
            .where(ReportAccessModel.report_id == report_id)
            .where(GroupMemberModel.user_id == user_id)
        )

        combined = union_all(direct_q, group_q)
        result = await self._session.execute(combined)
        levels = result.scalars().all()

        if not levels:
            return None

        # Return highest level
        best_level: str | None = None
        best_rank = -1
        for level in levels:
            rank = LEVEL_HIERARCHY.get(level, -1)
            if rank > best_rank:
                best_rank = rank
                best_level = level
        return best_level

    async def get_report_visibility(self, report_id: str) -> str | None:
        result = await self._session.execute(
            select(ReportModel.visibility).where(ReportModel.id == report_id)
        )
        return result.scalar_one_or_none()

    async def set_user_access(self, report_id: str, user_id: str, level: str) -> None:
        # Remove existing grant for this user on this report
        await self._session.execute(
            delete(ReportAccessModel)
            .where(ReportAccessModel.report_id == report_id)
            .where(ReportAccessModel.user_id == user_id)
        )
        self._session.add(
            ReportAccessModel(
                id=str(uuid4()),
                report_id=report_id,
                user_id=user_id,
                level=level,
            )
        )
        await self._session.commit()

    async def set_group_access(self, report_id: str, group_id: str, level: str) -> None:
        await self._session.execute(
            delete(ReportAccessModel)
            .where(ReportAccessModel.report_id == report_id)
            .where(ReportAccessModel.group_id == group_id)
        )
        self._session.add(
            ReportAccessModel(
                id=str(uuid4()),
                report_id=report_id,
                group_id=group_id,
                level=level,
            )
        )
        await self._session.commit()

    async def remove_user_access(self, report_id: str, user_id: str) -> None:
        await self._session.execute(
            delete(ReportAccessModel)
            .where(ReportAccessModel.report_id == report_id)
            .where(ReportAccessModel.user_id == user_id)
        )
        await self._session.commit()

    async def remove_group_access(self, report_id: str, group_id: str) -> None:
        await self._session.execute(
            delete(ReportAccessModel)
            .where(ReportAccessModel.report_id == report_id)
            .where(ReportAccessModel.group_id == group_id)
        )
        await self._session.commit()

    async def list_collaborators(self, report_id: str) -> list[AccessGrant]:
        result = await self._session.execute(
            select(ReportAccessModel).where(
                ReportAccessModel.report_id == report_id
            )
        )
        return [self._to_domain(r) for r in result.scalars().all()]
