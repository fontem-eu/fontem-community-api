from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4

from src.domain.dossier import Dossier
from src.repositories.dossier_repository import DossierRepository


class InMemoryDossierRepository(DossierRepository):
    def __init__(self) -> None:
        self._d: dict[str, Dossier] = {}

    async def create(self, dossier: Dossier) -> Dossier:
        if dossier.id is None:
            dossier.id = str(uuid4())
        dossier.created_at = dossier.created_at or datetime.now(timezone.utc)
        self._d[dossier.id] = deepcopy(dossier)
        return deepcopy(dossier)

    async def get_by_id(self, dossier_id: str) -> Dossier | None:
        d = self._d.get(dossier_id)
        return deepcopy(d) if d else None

    async def update(self, dossier: Dossier) -> Dossier:
        assert dossier.id is not None
        self._d[dossier.id] = deepcopy(dossier)
        return deepcopy(dossier)

    async def delete(self, dossier_id: str) -> None:
        self._d.pop(dossier_id, None)

    async def list_for_user(self, user_id: str) -> list[Dossier]:
        return [deepcopy(d) for d in self._d.values() if d.created_by == user_id]

    async def set_investigation(self, dossier_id: str, investigation_id: str | None) -> None:
        d = self._d.get(dossier_id)
        if d is not None:
            d.investigation_id = investigation_id

    async def list_by_investigation(self, investigation_id: str) -> list[Dossier]:
        return [deepcopy(d) for d in self._d.values()
                if d.investigation_id == investigation_id]
