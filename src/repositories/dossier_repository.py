from __future__ import annotations

from abc import ABC, abstractmethod

from src.domain.dossier import Dossier


class DossierRepository(ABC):
    @abstractmethod
    async def create(self, dossier: Dossier) -> Dossier: ...

    @abstractmethod
    async def get_by_id(self, dossier_id: str) -> Dossier | None: ...

    @abstractmethod
    async def update(self, dossier: Dossier) -> Dossier: ...

    @abstractmethod
    async def delete(self, dossier_id: str) -> None: ...

    @abstractmethod
    async def list_for_user(self, user_id: str) -> list[Dossier]: ...
