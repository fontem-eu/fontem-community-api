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
from src.repositories.resource_grant_repository import ResourceGrantRepository
from src.repositories.user_repository import UserRepository
from src.services.activity_service import ActivityService
from src.services.authz import Action, AuthorizationService, ResourceRef
from src.services.effective_access import _effective_access
from src.services.permission_service import LEVEL_HIERARCHY
from src.services.exceptions import InvalidInput, NotFound


class DossierService:
    def __init__(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        dossiers: DossierRepository,
        reports: ReportRepository,
        authz: AuthorizationService,
        investigations: InvestigationRepository,
        grants: ResourceGrantRepository,
        users: UserRepository,
        activity: ActivityService,
    ) -> None:
        self._dossiers = dossiers
        self._activity = activity
        self._reports = reports
        self._authz = authz
        self._inv = investigations
        self._grants = grants
        self._users = users

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
        grant = await self._grants.get_level("dossier", dossier.id, user_id)  # type: ignore[arg-type]
        principal = await self._authz.principal(user_id)
        await self._authz.require(
            principal, action,
            ResourceRef.for_dossier(dossier, member_role=member_role, effective_grant=grant),
        )

    async def share(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self, user_id: str, dossier_id: str,
        target_user_id: str | None = None, *, target_email: str | None = None,
        level: str = "viewer",
    ) -> None:
        """Grant a user direct access to the dossier (the additive override)."""
        d = await self._load(dossier_id)
        await self._require(user_id, d, Action.DOSSIERS_SHARE)
        if level not in LEVEL_HIERARCHY:
            raise InvalidInput(
                f"'{level}' is not a valid access level (viewer, commenter, editor, owner)."
            )
        target = await self._resolve_user(target_user_id, target_email)
        await self._grants.set_grant("dossier", dossier_id, target, level)

    async def revoke(self, user_id: str, dossier_id: str, target_user_id: str) -> None:
        d = await self._load(dossier_id)
        await self._require(user_id, d, Action.DOSSIERS_SHARE)
        await self._grants.remove_grant("dossier", dossier_id, target_user_id)

    async def list_grants(self, user_id: str, dossier_id: str) -> list[dict]:
        d = await self._load(dossier_id)
        await self._require(user_id, d, Action.DOSSIERS_READ)
        out = []
        for g in await self._grants.list_grants("dossier", dossier_id):
            u = await self._users.get_by_id(g.user_id)
            out.append({
                "user_id": g.user_id, "level": g.level,
                "email": u.email if u else None, "name": u.name if u else None,
            })
        return out

    async def _resolve_user(self, target_user_id: str | None, target_email: str | None) -> str:
        if target_user_id:
            u = await self._users.get_by_id(target_user_id)
        elif target_email:
            u = await self._users.get_by_email(target_email.strip().lower())
        else:
            raise InvalidInput("must supply target user_id or email")
        if u is None:
            raise NotFound("Target user not found")
        return u.id

    async def effective_access(self, user_id: str, dossier_id: str) -> list[dict]:
        """Who has access and why: each principal's highest level + its source
        (owner / inherited:<role> / direct). Gated by READ."""
        d = await self._load(dossier_id)
        await self._require(user_id, d, Action.DOSSIERS_READ)
        return await _effective_access(
            self._inv, self._grants, self._users, "dossier", dossier_id,
            d.created_by, d.investigation_id,
        )


    async def create(
        self, user_id: str, name: str, investigation_id: str | None = None,
    ) -> Dossier:
        principal = await self._authz.principal(user_id)
        await self._authz.require(principal, Action.DOSSIERS_CREATE)
        if not name.strip():
            raise InvalidInput("Dossier name cannot be empty")
        d = await self._dossiers.create(Dossier(
            name=name.strip(), investigation_id=investigation_id, created_by=user_id,
        ))
        await self._activity.record(user_id, "dossier", d.id or "", "created", d.name)
        return d

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
        updated = await self._dossiers.update(d)
        await self._activity.record(user_id, "dossier", dossier_id, "updated", updated.name)
        return updated

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
        await self._activity.record(user_id, "dossier", dossier_id, "deleted", d.name)

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
