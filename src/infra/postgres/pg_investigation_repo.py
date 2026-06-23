# ``sqlalchemy.func.count`` is a magic factory pylint can't introspect;
# it sees only the descriptor and flags every ``func.count()`` call as
# not-callable. Disable module-wide (mirrors pg_tag_follow_repo/pg_report_repo).
# pylint: disable=not-callable
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.investigation import Investigation, InvestigationMember
from src.infra.postgres.models import InvestigationMemberModel, InvestigationModel
from src.repositories.investigation_repository import InvestigationRepository


class PgInvestigationRepository(InvestigationRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _to_domain(row: InvestigationModel) -> Investigation:
        return Investigation(
            id=row.id,
            name=row.name,
            description=row.description,
            created_by=row.created_by,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _member_to_domain(row: InvestigationMemberModel) -> InvestigationMember:
        return InvestigationMember(
            investigation_id=row.investigation_id,
            user_id=row.user_id,
            can_write_stories=row.can_write_stories,
            can_add_viz=row.can_add_viz,
            can_administer=row.can_administer,
            is_owner=row.is_owner,
        )

    async def create(self, investigation: Investigation) -> Investigation:
        now = datetime.now(timezone.utc)
        model = InvestigationModel(
            id=investigation.id or str(uuid4()),
            name=investigation.name,
            description=investigation.description,
            created_by=investigation.created_by,
            created_at=investigation.created_at or now,
            updated_at=investigation.updated_at or now,
        )
        self._session.add(model)
        await self._session.commit()
        return self._to_domain(model)

    async def get_by_id(self, investigation_id: str) -> Investigation | None:
        result = await self._session.execute(
            select(InvestigationModel).where(InvestigationModel.id == investigation_id)
        )
        row = result.scalar_one_or_none()
        return self._to_domain(row) if row else None

    async def update(self, investigation: Investigation) -> Investigation:
        await self._session.execute(
            InvestigationModel.__table__.update()
            .where(InvestigationModel.id == investigation.id)
            .values(
                name=investigation.name,
                description=investigation.description,
                updated_at=datetime.now(timezone.utc),
            )
        )
        await self._session.commit()
        refreshed = await self.get_by_id(investigation.id)  # type: ignore[arg-type]
        assert refreshed is not None
        return refreshed

    async def delete(self, investigation_id: str) -> None:
        await self._session.execute(
            delete(InvestigationModel).where(InvestigationModel.id == investigation_id)
        )
        await self._session.commit()

    async def list_for_user(self, user_id: str) -> list[Investigation]:
        result = await self._session.execute(
            select(InvestigationModel)
            .join(
                InvestigationMemberModel,
                InvestigationMemberModel.investigation_id == InvestigationModel.id,
            )
            .where(InvestigationMemberModel.user_id == user_id)
        )
        return [self._to_domain(r) for r in result.scalars().all()]

    async def upsert_member(self, member: InvestigationMember) -> None:
        existing = await self.get_member(member.investigation_id, member.user_id)
        if existing is None:
            self._session.add(
                InvestigationMemberModel(
                    investigation_id=member.investigation_id,
                    user_id=member.user_id,
                    can_write_stories=member.can_write_stories,
                    can_add_viz=member.can_add_viz,
                    can_administer=member.can_administer,
                    is_owner=member.is_owner,
                )
            )
        else:
            await self._session.execute(
                InvestigationMemberModel.__table__.update()
                .where(InvestigationMemberModel.investigation_id == member.investigation_id)
                .where(InvestigationMemberModel.user_id == member.user_id)
                .values(
                    can_write_stories=member.can_write_stories,
                    can_add_viz=member.can_add_viz,
                    can_administer=member.can_administer,
                    is_owner=member.is_owner,
                )
            )
        await self._session.commit()

    async def get_member(
        self, investigation_id: str, user_id: str,
    ) -> InvestigationMember | None:
        result = await self._session.execute(
            select(InvestigationMemberModel)
            .where(InvestigationMemberModel.investigation_id == investigation_id)
            .where(InvestigationMemberModel.user_id == user_id)
        )
        row = result.scalar_one_or_none()
        return self._member_to_domain(row) if row else None

    async def list_members(self, investigation_id: str) -> list[InvestigationMember]:
        result = await self._session.execute(
            select(InvestigationMemberModel).where(
                InvestigationMemberModel.investigation_id == investigation_id
            )
        )
        return [self._member_to_domain(r) for r in result.scalars().all()]

    async def remove_member(self, investigation_id: str, user_id: str) -> None:
        await self._session.execute(
            delete(InvestigationMemberModel)
            .where(InvestigationMemberModel.investigation_id == investigation_id)
            .where(InvestigationMemberModel.user_id == user_id)
        )
        await self._session.commit()

    async def count_owners(self, investigation_id: str) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(InvestigationMemberModel)
            .where(InvestigationMemberModel.investigation_id == investigation_id)
            .where(InvestigationMemberModel.is_owner.is_(True))
        )
        return int(result.scalar_one())
