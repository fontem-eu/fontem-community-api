from __future__ import annotations

from abc import ABC, abstractmethod

from src.domain.investigation import Investigation, InvestigationMember


class InvestigationRepository(ABC):
    @abstractmethod
    async def create(self, investigation: Investigation) -> Investigation: ...

    @abstractmethod
    async def get_by_id(self, investigation_id: str) -> Investigation | None: ...

    @abstractmethod
    async def update(self, investigation: Investigation) -> Investigation: ...

    @abstractmethod
    async def delete(self, investigation_id: str) -> None: ...

    @abstractmethod
    async def list_for_user(self, user_id: str) -> list[Investigation]: ...

    # ── membership ──
    @abstractmethod
    async def upsert_member(self, member: InvestigationMember) -> None: ...

    @abstractmethod
    async def get_member(
        self, investigation_id: str, user_id: str,
    ) -> InvestigationMember | None: ...

    @abstractmethod
    async def list_members(self, investigation_id: str) -> list[InvestigationMember]: ...

    @abstractmethod
    async def remove_member(self, investigation_id: str, user_id: str) -> None: ...

    @abstractmethod
    async def count_owners(self, investigation_id: str) -> int: ...
