"""Visualization service — server-side saved viz (the pocket's successor).

A viz is owned by its creator (CRUD is owner-gated). Attaching/detaching to an
investigation reuses the investigation capability gate (INVESTIGATIONS_ADD_VIZ =
``can_add_viz``); listing an investigation's viz needs INVESTIGATIONS_READ.
"""
from __future__ import annotations

from src.domain.visualization import Visualization
from src.repositories.investigation_repository import InvestigationRepository
from src.repositories.visualization_repository import VisualizationRepository
from src.services.authz import Action, AuthorizationService, ResourceRef
from src.services.exceptions import InvalidInput, NotFound


class VisualizationService:
    def __init__(
        self,
        visualizations: VisualizationRepository,
        investigations: InvestigationRepository,
        authz: AuthorizationService,
    ) -> None:
        self._viz = visualizations
        self._inv = investigations
        self._authz = authz

    async def _load(self, viz_id: str) -> Visualization:
        v = await self._viz.get_by_id(viz_id)
        if v is None:
            raise NotFound(f"Visualization {viz_id} not found")
        return v

    async def _require_viz(self, user_id: str, viz: Visualization, action: Action) -> None:
        principal = await self._authz.principal(user_id)
        await self._authz.require(principal, action, ResourceRef.for_visualization(viz))

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
            # Saving straight onto an investigation needs the can_add_viz cap.
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
