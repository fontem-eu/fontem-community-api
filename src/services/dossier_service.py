"""Dossier service — the single place dossier state changes.

A dossier is a thin tree-of-articles. It's owned by its creator; every
mutation is owner-gated through the AuthorizationService. Articles are
placed via report_repo.set_dossier (dossier_id + parent_id); the articles
keep their own report_access permissions — a dossier only structures them.
"""
from __future__ import annotations

from src.domain.dossier import Dossier
from src.repositories.dossier_repository import DossierRepository
from src.repositories.investigation_repository import InvestigationRepository
from src.repositories.report_repository import ReportRepository
from src.services.authz import Action, AuthorizationService, ResourceRef
from src.services.exceptions import InvalidInput, NotFound


class DossierService:
    def __init__(
        self,
        dossiers: DossierRepository,
        reports: ReportRepository,
        authz: AuthorizationService,
        investigations: InvestigationRepository,
    ) -> None:
        self._dossiers = dossiers
        self._reports = reports
        self._authz = authz
        self._inv = investigations

    async def _load(self, dossier_id: str) -> Dossier:
        d = await self._dossiers.get_by_id(dossier_id)
        if d is None:
            raise NotFound(f"Dossier {dossier_id} not found")
        return d

    async def _require(self, user_id: str, dossier: Dossier, action: Action) -> None:
        member_role = None
        if dossier.investigation_id:
            m = await self._inv.get_member(dossier.investigation_id, user_id)
            member_role = m.role if m is not None else None
        principal = await self._authz.principal(user_id)
        await self._authz.require(
            principal, action, ResourceRef.for_dossier(dossier, member_role=member_role),
        )

    async def create(
        self, user_id: str, name: str, investigation_id: str | None = None,
    ) -> Dossier:
        principal = await self._authz.principal(user_id)
        await self._authz.require(principal, Action.DOSSIERS_CREATE)
        if not name.strip():
            raise InvalidInput("Dossier name cannot be empty")
        return await self._dossiers.create(Dossier(
            name=name.strip(), investigation_id=investigation_id, created_by=user_id,
        ))

    async def get(self, user_id: str, dossier_id: str) -> Dossier:
        d = await self._load(dossier_id)
        await self._require(user_id, d, Action.DOSSIERS_READ)
        return d

    async def list_mine(self, user_id: str) -> list[Dossier]:
        return await self._dossiers.list_for_user(user_id)

    async def update(self, user_id: str, dossier_id: str, name: str) -> Dossier:
        d = await self._load(dossier_id)
        await self._require(user_id, d, Action.DOSSIERS_EDIT)
        if not name.strip():
            raise InvalidInput("Dossier name cannot be empty")
        d.name = name.strip()
        return await self._dossiers.update(d)

    async def delete(self, user_id: str, dossier_id: str, content: str = "orphan") -> None:
        if content not in ("cascade", "orphan"):
            raise InvalidInput("content must be 'cascade' or 'orphan'")
        d = await self._load(dossier_id)
        await self._require(user_id, d, Action.DOSSIERS_DELETE)
        articles = await self._reports.list_by_dossier(dossier_id)
        for art in articles:
            if content == "cascade":
                await self._reports.delete(art.id)  # type: ignore[arg-type]
            else:  # orphan — detach from the dossier, keep the article
                await self._reports.set_dossier(art.id, None, None)  # type: ignore[arg-type]
        await self._dossiers.delete(dossier_id)

    async def tree(self, user_id: str, dossier_id: str) -> list[dict]:
        """Articles in the dossier as flat tree nodes ({id, title, parent_id});
        the client assembles the tree."""
        d = await self._load(dossier_id)
        await self._require(user_id, d, Action.DOSSIERS_READ)
        articles = await self._reports.list_by_dossier(dossier_id)
        return [{"id": a.id, "title": a.title, "parent_id": a.parent_id} for a in articles]

    async def add_article(
        self, user_id: str, dossier_id: str, report_id: str,
        parent_id: str | None = None,
    ) -> None:
        d = await self._load(dossier_id)
        await self._require(user_id, d, Action.DOSSIERS_ADD_ARTICLE)
        report = await self._reports.get_by_id(report_id)
        if report is None:
            raise NotFound(f"Article {report_id} not found")
        await self._reports.set_dossier(report_id, dossier_id, parent_id)

    async def remove_article(self, user_id: str, dossier_id: str, report_id: str) -> None:
        d = await self._load(dossier_id)
        await self._require(user_id, d, Action.DOSSIERS_REMOVE_ARTICLE)
        await self._reports.set_dossier(report_id, None, None)
