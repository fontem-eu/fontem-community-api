"""Visualization service — server-side saved viz (the pocket's successor).

A viz is owned by its creator (CRUD is owner-gated). Attaching/detaching to an
investigation reuses the investigation capability gate (INVESTIGATIONS_ADD_VIZ =
contributor role); listing an investigation's viz needs INVESTIGATIONS_READ.
"""
from __future__ import annotations

from src.domain.visualization import Visualization
from src.repositories.investigation_repository import InvestigationRepository
from src.repositories.resource_grant_repository import ResourceGrantRepository
from src.repositories.user_repository import UserRepository
from src.repositories.visualization_repository import VisualizationRepository
from src.services.authz import Action, AuthorizationService, ResourceRef
from src.services.effective_access import _effective_access
from src.services.permission_service import LEVEL_HIERARCHY
from src.services.share_targets import resolve_share_target
from src.services.exceptions import InvalidInput, NotFound


class VisualizationService:
    def __init__(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        visualizations: VisualizationRepository,
        investigations: InvestigationRepository,
        authz: AuthorizationService,
        grants: ResourceGrantRepository,
        users: UserRepository,
    ) -> None:
        self._viz = visualizations
        self._inv = investigations
        self._authz = authz
        self._grants = grants
        self._users = users

    async def _load(self, viz_id: str) -> Visualization:
        v = await self._viz.get_by_id(viz_id)
        if v is None:
            raise NotFound(f"Visualization {viz_id} not found")
        return v

    async def _require_viz(self, user_id: str, viz: Visualization, action: Action) -> None:
        member_role = None
        if viz.investigation_id:
            m = await self._inv.get_member(viz.investigation_id, user_id)
            member_role = m.role if m is not None else None
        grant = await self._grants.get_level("visualization", viz.id, user_id)  # type: ignore[arg-type]
        principal = await self._authz.principal(user_id)
        await self._authz.require(
            principal, action,
            ResourceRef.for_visualization(viz, member_role=member_role, effective_grant=grant),
        )

    async def share(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self, user_id: str, viz_id: str,
        target_user_id: str | None = None, *, target_email: str | None = None,
        level: str = "viewer",
    ) -> None:
        v = await self._load(viz_id)
        await self._require_viz(user_id, v, Action.VISUALIZATIONS_SHARE)
        if level not in LEVEL_HIERARCHY:
            raise InvalidInput(
                f"'{level}' is not a valid access level (viewer, commenter, editor, owner)."
            )
        target = await resolve_share_target(self._users, target_user_id, target_email)
        if target is not None:  # unknown email -> uniform no-op (no enumeration oracle)
            await self._grants.set_grant("visualization", viz_id, target, level)

    async def revoke(self, user_id: str, viz_id: str, target_user_id: str) -> None:
        v = await self._load(viz_id)
        await self._require_viz(user_id, v, Action.VISUALIZATIONS_SHARE)
        await self._grants.remove_grant("visualization", viz_id, target_user_id)

    async def list_grants(self, user_id: str, viz_id: str) -> list[dict]:
        v = await self._load(viz_id)
        await self._require_viz(user_id, v, Action.VISUALIZATIONS_READ)
        out = []
        for g in await self._grants.list_grants("visualization", viz_id):
            u = await self._users.get_by_id(g.user_id)
            out.append({
                "user_id": g.user_id, "level": g.level,
                "email": u.email if u else None, "name": u.name if u else None,
            })
        return out


    async def effective_access(self, user_id: str, viz_id: str) -> list[dict]:
        """Who has access and why (owner / inherited:<role> / direct). READ-gated."""
        v = await self._load(viz_id)
        await self._require_viz(user_id, v, Action.VISUALIZATIONS_READ)
        return await _effective_access(
            self._inv, self._grants, self._users, "visualization", viz_id,
            v.created_by, v.investigation_id,
        )


    async def _require_inv(self, user_id: str, investigation_id: str, action: Action) -> None:
        inv = await self._inv.get_by_id(investigation_id)
        if inv is None:
            raise NotFound(f"Investigation {investigation_id} not found")
        membership = await self._inv.get_member(investigation_id, user_id)
        principal = await self._authz.principal(user_id)
        await self._authz.require(
            principal, action, ResourceRef.for_investigation(inv, membership),
        )

    async def create(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self, user_id: str, name: str, widget_type: str,
        config: dict, investigation_id: str | None = None,
    ) -> Visualization:
        principal = await self._authz.principal(user_id)
        await self._authz.require(principal, Action.VISUALIZATIONS_CREATE)
        if not widget_type.strip():
            raise InvalidInput("widget_type is required")
        if investigation_id is not None:
            # Saving straight onto an investigation needs the contributor role.
            await self._require_inv(user_id, investigation_id, Action.INVESTIGATIONS_ADD_VIZ)
        return await self._viz.create(Visualization(
            name=(name or "").strip() or widget_type,
            widget_type=widget_type,
            config=config or {},
            created_by=user_id,
            investigation_id=investigation_id,
        ))

    async def get(self, user_id: str, viz_id: str) -> Visualization:
        v = await self._load(viz_id)
        await self._require_viz(user_id, v, Action.VISUALIZATIONS_READ)
        return v

    async def list_mine(self, user_id: str) -> list[Visualization]:
        return await self._viz.list_for_user(user_id)

    async def update(self, user_id: str, viz_id: str, name: str) -> Visualization:
        v = await self._load(viz_id)
        await self._require_viz(user_id, v, Action.VISUALIZATIONS_EDIT)
        v.name = (name or "").strip() or v.name
        return await self._viz.update(v)

    async def delete(self, user_id: str, viz_id: str) -> None:
        v = await self._load(viz_id)
        await self._require_viz(user_id, v, Action.VISUALIZATIONS_DELETE)
        await self._viz.delete(viz_id)

    async def attach(self, user_id: str, viz_id: str, investigation_id: str) -> None:
        v = await self._load(viz_id)
        await self._require_viz(user_id, v, Action.VISUALIZATIONS_EDIT)
        await self._require_inv(user_id, investigation_id, Action.INVESTIGATIONS_ADD_VIZ)
        await self._viz.set_investigation(viz_id, investigation_id)

    async def detach(self, user_id: str, viz_id: str) -> None:
        v = await self._load(viz_id)
        await self._require_viz(user_id, v, Action.VISUALIZATIONS_EDIT)
        await self._viz.set_investigation(viz_id, None)

    async def list_for_investigation(self, user_id: str, investigation_id: str) -> list[Visualization]:
        await self._require_inv(user_id, investigation_id, Action.INVESTIGATIONS_READ)
        return await self._viz.list_by_investigation(investigation_id)
