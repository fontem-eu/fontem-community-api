"""Investigation service — the single place investigation state changes.

Every mutation runs through the AuthorizationService (policy in
src/services/authz/policy.py). The owner *invariants* — "an owner can't
change/remove another owner", "always >= 1 owner", "only an owner can grant
owner" — are enforced here (they depend on the target member's state, which
the pure policy can't see), each with its own test.
"""
from __future__ import annotations

from src.domain.investigation import Investigation, InvestigationMember
from src.repositories.dossier_repository import DossierRepository
from src.repositories.investigation_repository import InvestigationRepository
from src.repositories.report_repository import ReportRepository
from src.repositories.visualization_repository import VisualizationRepository
from src.repositories.user_repository import UserRepository
from src.services.authz import Action, AuthorizationService, ResourceRef
from src.services.exceptions import Conflict, InvalidInput, NotFound, PermissionDenied


class InvestigationService:
    def __init__(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        investigations: InvestigationRepository,
        users: UserRepository,
        authz: AuthorizationService,
        reports: ReportRepository,
        dossiers: DossierRepository,
        visualizations: VisualizationRepository,
    ) -> None:
        self._inv = investigations
        self._users = users
        self._authz = authz
        self._reports = reports
        self._dossiers = dossiers
        self._viz = visualizations

    async def _load(self, investigation_id: str) -> Investigation:
        inv = await self._inv.get_by_id(investigation_id)
        if inv is None:
            raise NotFound(f"Investigation {investigation_id} not found")
        return inv

    async def _require(
        self, user_id: str, investigation_id: str, action: Action,
    ) -> Investigation:
        """Load the investigation and authorize ``action`` for ``user_id``
        against it (membership-aware)."""
        inv = await self._load(investigation_id)
        membership = await self._inv.get_member(investigation_id, user_id)
        principal = await self._authz.principal(user_id)
        await self._authz.require(
            principal, action, ResourceRef.for_investigation(inv, membership),
        )
        return inv

    # ── investigation CRUD ──
    async def create(self, user_id: str, name: str, description: str = "") -> Investigation:
        principal = await self._authz.principal(user_id)
        await self._authz.require(principal, Action.INVESTIGATIONS_CREATE)
        if not name.strip():
            raise InvalidInput("Investigation name cannot be empty")
        inv = await self._inv.create(
            Investigation(name=name.strip(), description=description, created_by=user_id)
        )
        # The creator is the founding owner, holding every capability.
        await self._inv.upsert_member(InvestigationMember(
            investigation_id=inv.id,  # type: ignore[arg-type]
            user_id=user_id,
            can_write_stories=True, can_add_viz=True, can_administer=True, is_owner=True,
        ))
        return inv

    async def get(self, user_id: str | None, investigation_id: str) -> Investigation:
        inv = await self._load(investigation_id)
        membership = (
            await self._inv.get_member(investigation_id, user_id) if user_id else None
        )
        principal = await self._authz.principal(user_id)
        await self._authz.require(
            principal, Action.INVESTIGATIONS_READ,
            ResourceRef.for_investigation(inv, membership),
        )
        return inv

    async def list_mine(self, user_id: str) -> list[Investigation]:
        return await self._inv.list_for_user(user_id)

    async def my_membership(
        self, user_id: str, investigation_id: str,
    ) -> InvestigationMember | None:
        return await self._inv.get_member(investigation_id, user_id)

    async def update_meta(
        self, user_id: str, investigation_id: str,
        name: str | None = None, description: str | None = None,
    ) -> Investigation:
        inv = await self._load(investigation_id)
        membership = await self._inv.get_member(investigation_id, user_id)
        principal = await self._authz.principal(user_id)
        await self._authz.require(
            principal, Action.INVESTIGATIONS_EDIT_META,
            ResourceRef.for_investigation(inv, membership),
        )
        if name is not None:
            if not name.strip():
                raise InvalidInput("Investigation name cannot be empty")
            inv.name = name.strip()
        if description is not None:
            inv.description = description
        return await self._inv.update(inv)

    async def delete(
        self, user_id: str, investigation_id: str, content: str = "orphan",
    ) -> None:
        if content not in ("cascade", "orphan"):
            raise InvalidInput("content must be 'cascade' or 'orphan'")
        inv = await self._load(investigation_id)
        membership = await self._inv.get_member(investigation_id, user_id)
        principal = await self._authz.principal(user_id)
        await self._authz.require(
            principal, Action.INVESTIGATIONS_DELETE,
            ResourceRef.for_investigation(inv, membership),
        )
        # `content` governs the articles + dossiers + viz this investigation
        # aggregates: cascade removes them, orphan just detaches them.
        if content == "cascade":
            await self._cascade_content(investigation_id)
        else:
            await self._orphan_content(investigation_id)
        await self._inv.delete(investigation_id)

    async def _cascade_content(self, investigation_id: str) -> None:
        """Delete contained dossiers (with their articles), loose articles, and
        viz linked to the investigation."""
        for d in await self._dossiers.list_by_investigation(investigation_id):
            for art in await self._reports.list_by_dossier(d.id):  # type: ignore[arg-type]
                await self._reports.delete(art.id)  # type: ignore[arg-type]
            await self._dossiers.delete(d.id)  # type: ignore[arg-type]
        for art in await self._reports.list_by_investigation(investigation_id):
            await self._reports.delete(art.id)  # type: ignore[arg-type]
        for v in await self._viz.list_by_investigation(investigation_id):
            await self._viz.delete(v.id)  # type: ignore[arg-type]

    async def _orphan_content(self, investigation_id: str) -> None:
        """Detach the investigation's articles, dossiers, and viz (kept)."""
        for art in await self._reports.list_by_investigation(investigation_id):
            await self._reports.set_investigation(art.id, None)  # type: ignore[arg-type]
        for d in await self._dossiers.list_by_investigation(investigation_id):
            await self._dossiers.set_investigation(d.id, None)  # type: ignore[arg-type]
        for v in await self._viz.list_by_investigation(investigation_id):
            await self._viz.set_investigation(v.id, None)  # type: ignore[arg-type]

    # ── articles (stories) ──
    async def add_story(self, user_id: str, investigation_id: str, report_id: str) -> None:
        """Link an article to the investigation. Gated by `can_write_stories`."""
        await self._require(user_id, investigation_id, Action.INVESTIGATIONS_ADD_STORY)
        report = await self._reports.get_by_id(report_id)
        if report is None:
            raise NotFound(f"Article {report_id} not found")
        await self._reports.set_investigation(report_id, investigation_id)

    async def remove_story(self, user_id: str, investigation_id: str, report_id: str) -> None:
        """Detach an article from the investigation (the article survives)."""
        await self._require(user_id, investigation_id, Action.INVESTIGATIONS_REMOVE_STORY)
        await self._reports.set_investigation(report_id, None)

    async def list_stories(self, user_id: str, investigation_id: str) -> list[dict]:
        await self._require(user_id, investigation_id, Action.INVESTIGATIONS_READ)
        articles = await self._reports.list_by_investigation(investigation_id)
        return [{"id": a.id, "title": a.title} for a in articles]

    # ── membership ──
    async def list_members(
        self, user_id: str, investigation_id: str,
    ) -> list[InvestigationMember]:
        inv = await self._load(investigation_id)
        membership = await self._inv.get_member(investigation_id, user_id)
        principal = await self._authz.principal(user_id)
        await self._authz.require(
            principal, Action.INVESTIGATIONS_READ,
            ResourceRef.for_investigation(inv, membership),
        )
        return await self._inv.list_members(investigation_id)

    async def set_member(  # pylint: disable=too-many-arguments
        self, user_id: str, investigation_id: str,
        target_user_id: str | None = None, *, target_email: str | None = None,
        can_write_stories: bool = False, can_add_viz: bool = False,
        can_administer: bool = False, is_owner: bool = False,
    ) -> None:
        """Add or update a member (identified by id or email) with the given
        capabilities, enforcing the owner invariants."""
        inv = await self._load(investigation_id)
        actor = await self._inv.get_member(investigation_id, user_id)
        principal = await self._authz.principal(user_id)
        await self._authz.require(
            principal, Action.INVESTIGATIONS_MANAGE_MEMBERS,
            ResourceRef.for_investigation(inv, actor),
        )
        is_platform_admin = principal is not None and "admin" in principal.roles
        if target_user_id:
            target = await self._users.get_by_id(target_user_id)
        elif target_email:
            target = await self._users.get_by_email(target_email.strip().lower())
        else:
            raise InvalidInput("must supply target user_id or email")
        if target is None:
            raise NotFound("Target user not found")
        target_user_id = target.id
        current = await self._inv.get_member(investigation_id, target_user_id)
        actor_is_owner = (
            is_platform_admin
            or inv.created_by == user_id
            or (actor is not None and actor.is_owner)
        )
        if current is not None and current.is_owner and target_user_id != user_id and not is_platform_admin:
            raise Conflict("an owner cannot change another owner's role")
        if is_owner and not actor_is_owner:
            raise PermissionDenied("only an owner can grant the owner role")
        if (
            current is not None and current.is_owner and not is_owner
            and await self._inv.count_owners(investigation_id) <= 1
        ):
            raise Conflict("an investigation must keep at least one owner")
        await self._inv.upsert_member(InvestigationMember(
            investigation_id=investigation_id, user_id=target_user_id,
            can_write_stories=can_write_stories, can_add_viz=can_add_viz,
            can_administer=can_administer, is_owner=is_owner,
        ))

    async def remove_member(
        self, user_id: str, investigation_id: str, target_user_id: str,
    ) -> None:
        inv = await self._load(investigation_id)
        actor = await self._inv.get_member(investigation_id, user_id)
        principal = await self._authz.principal(user_id)
        await self._authz.require(
            principal, Action.INVESTIGATIONS_MANAGE_MEMBERS,
            ResourceRef.for_investigation(inv, actor),
        )
        is_platform_admin = principal is not None and "admin" in principal.roles
        current = await self._inv.get_member(investigation_id, target_user_id)
        if current is None:
            return
        if current.is_owner and target_user_id != user_id and not is_platform_admin:
            raise Conflict("an owner cannot remove another owner")
        if current.is_owner and await self._inv.count_owners(investigation_id) <= 1:
            raise Conflict("an investigation must keep at least one owner")
        await self._inv.remove_member(investigation_id, target_user_id)
