from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.dossier import Dossier
from src.infra.postgres.models import DossierModel
from src.repositories.dossier_repository import DossierRepository


class PgDossierRepository(DossierRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _to_domain(row: DossierModel) -> Dossier:
        return Dossier(
            id=row.id,
            name=row.name,
            investigation_id=row.investigation_id,
            created_by=row.created_by,
            created_at=row.created_at,
        )

    async def create(self, dossier: Dossier) -> Dossier:
        model = DossierModel(
            id=dossier.id or str(uuid4()),
            name=dossier.name,
            investigation_id=dossier.investigation_id,
            created_by=dossier.created_by,
            created_at=dossier.created_at or datetime.now(timezone.utc),
        )
        self._session.add(model)
        await self._session.commit()
        return self._to_domain(model)

    async def get_by_id(self, dossier_id: str) -> Dossier | None:
        result = await self._session.execute(
            select(DossierModel).where(DossierModel.id == dossier_id)
        )
        row = result.scalar_one_or_none()
        return self._to_domain(row) if row else None

    async def update(self, dossier: Dossier) -> Dossier:
        await self._session.execute(
            DossierModel.__table__.update()
            .where(DossierModel.id == dossier.id)
            .values(name=dossier.name, investigation_id=dossier.investigation_id)
        )
        await self._session.commit()
        refreshed = await self.get_by_id(dossier.id)  # type: ignore[arg-type]
        assert refreshed is not None
        return refreshed

    async def delete(self, dossier_id: str) -> None:
        await self._session.execute(
            delete(DossierModel).where(DossierModel.id == dossier_id)
        )
        await self._session.commit()

    async def list_for_user(self, user_id: str) -> list[Dossier]:
        result = await self._session.execute(
            select(DossierModel).where(DossierModel.created_by == user_id)
        )
        return [self._to_domain(r) for r in result.scalars().all()]
