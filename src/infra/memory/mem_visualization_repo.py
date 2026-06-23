from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4

from src.domain.visualization import Visualization
from src.repositories.visualization_repository import VisualizationRepository


class InMemoryVisualizationRepository(VisualizationRepository):
    def __init__(self) -> None:
        self._v: dict[str, Visualization] = {}

    async def create(self, viz: Visualization) -> Visualization:
        if viz.id is None:
            viz.id = str(uuid4())
        viz.created_at = viz.created_at or datetime.now(timezone.utc)
        self._v[viz.id] = deepcopy(viz)
        return deepcopy(viz)

    async def get_by_id(self, viz_id: str) -> Visualization | None:
        v = self._v.get(viz_id)
        return deepcopy(v) if v else None

    async def update(self, viz: Visualization) -> Visualization:
        assert viz.id is not None
        self._v[viz.id] = deepcopy(viz)
        return deepcopy(viz)

    async def delete(self, viz_id: str) -> None:
        self._v.pop(viz_id, None)

    async def list_for_user(self, user_id: str) -> list[Visualization]:
        return [deepcopy(v) for v in self._v.values() if v.created_by == user_id]

    async def list_by_investigation(self, investigation_id: str) -> list[Visualization]:
        return [deepcopy(v) for v in self._v.values()
                if v.investigation_id == investigation_id]

    async def set_investigation(self, viz_id: str, investigation_id: str | None) -> None:
        v = self._v.get(viz_id)
        if v is not None:
            v.investigation_id = investigation_id
