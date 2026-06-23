"""Access inheritance — an investigation confers access to the articles,
dossiers and viz it contains. A member's investigation role maps to a level
(ROLE_TO_LEVEL) that composes (via max) with any direct grant on the resource.
Additive only: inheritance can grant more, never less.
"""
from __future__ import annotations

from src.domain.investigation_roles import ROLE_TO_LEVEL
from src.repositories.dossier_repository import DossierRepository
from src.repositories.investigation_repository import InvestigationRepository
from src.services.permission_service import LEVEL_HIERARCHY


def max_level(a: str | None, b: str | None) -> str | None:
    """Higher of two report-access levels (viewer<commenter<editor<owner)."""
    if a is None:
        return b
    if b is None:
        return a
    return a if LEVEL_HIERARCHY.get(a, 0) >= LEVEL_HIERARCHY.get(b, 0) else b


class AccessInheritance:
    def __init__(
        self, investigations: InvestigationRepository, dossiers: DossierRepository,
    ) -> None:
        self._inv = investigations
        self._dossiers = dossiers

    async def inherited_role(self, user_id: str | None, investigation_id: str | None) -> str | None:
        """The caller's role in ``investigation_id`` (None if not a member)."""
        if not investigation_id or not user_id:
            return None
        member = await self._inv.get_member(investigation_id, user_id)
        return member.role if member is not None else None

    async def inherited_level(self, user_id: str | None, investigation_id: str | None) -> str | None:
        role = await self.inherited_role(user_id, investigation_id)
        return ROLE_TO_LEVEL.get(role) if role else None

    async def investigation_of_report(self, report) -> str | None:
        """An article's investigation: its direct link, else its dossier's."""
        if getattr(report, "investigation_id", None):
            return report.investigation_id
        if getattr(report, "dossier_id", None):
            d = await self._dossiers.get_by_id(report.dossier_id)
            return d.investigation_id if d is not None else None
        return None

    async def inherited_report_level(self, user_id: str | None, report) -> str | None:
        inv_id = await self.investigation_of_report(report)
        return await self.inherited_level(user_id, inv_id)
