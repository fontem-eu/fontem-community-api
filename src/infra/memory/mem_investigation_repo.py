from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4

from src.domain.investigation import Investigation, InvestigationMember
from src.repositories.investigation_repository import InvestigationRepository


class InMemoryInvestigationRepository(InvestigationRepository):
    def __init__(self) -> None:
        self._inv: dict[str, Investigation] = {}
        # investigation_id -> {user_id -> InvestigationMember}
        self._members: dict[str, dict[str, InvestigationMember]] = {}

    async def create(self, investigation: Investigation) -> Investigation:
        if investigation.id is None:
            investigation.id = str(uuid4())
        now = datetime.now(timezone.utc)
        investigation.created_at = investigation.created_at or now
        investigation.updated_at = investigation.updated_at or now
        self._inv[investigation.id] = deepcopy(investigation)
        self._members.setdefault(investigation.id, {})
        return deepcopy(investigation)

    async def get_by_id(self, investigation_id: str) -> Investigation | None:
        inv = self._inv.get(investigation_id)
        return deepcopy(inv) if inv else None

    async def update(self, investigation: Investigation) -> Investigation:
        assert investigation.id is not None
        investigation.updated_at = datetime.now(timezone.utc)
        self._inv[investigation.id] = deepcopy(investigation)
        return deepcopy(investigation)

    async def delete(self, investigation_id: str) -> None:
        self._inv.pop(investigation_id, None)
        self._members.pop(investigation_id, None)

    async def list_for_user(self, user_id: str) -> list[Investigation]:
        out: list[Investigation] = []
        for inv_id, members in self._members.items():
            if user_id in members and inv_id in self._inv:
                out.append(deepcopy(self._inv[inv_id]))
        return out

    async def upsert_member(self, member: InvestigationMember) -> None:
        self._members.setdefault(member.investigation_id, {})[member.user_id] = deepcopy(member)

    async def get_member(
        self, investigation_id: str, user_id: str,
    ) -> InvestigationMember | None:
        m = self._members.get(investigation_id, {}).get(user_id)
        return deepcopy(m) if m else None

    async def list_members(self, investigation_id: str) -> list[InvestigationMember]:
        return [deepcopy(m) for m in self._members.get(investigation_id, {}).values()]

    async def remove_member(self, investigation_id: str, user_id: str) -> None:
        self._members.get(investigation_id, {}).pop(user_id, None)

    async def count_owners(self, investigation_id: str) -> int:
        return sum(
            1 for m in self._members.get(investigation_id, {}).values() if m.is_owner
        )
