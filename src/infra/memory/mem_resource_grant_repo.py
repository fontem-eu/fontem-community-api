from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

from src.domain.resource_grant import ResourceGrant
from src.repositories.resource_grant_repository import ResourceGrantRepository


class InMemoryResourceGrantRepository(ResourceGrantRepository):
    def __init__(self) -> None:
        self._g: dict[tuple[str, str, str], ResourceGrant] = {}

    async def set_grant(self, resource_type: str, resource_id: str, user_id: str, level: str) -> None:
        self._g[(resource_type, resource_id, user_id)] = ResourceGrant(
            resource_type=resource_type, resource_id=resource_id, user_id=user_id,
            level=level, created_at=datetime.now(timezone.utc),
        )

    async def remove_grant(self, resource_type: str, resource_id: str, user_id: str) -> None:
        self._g.pop((resource_type, resource_id, user_id), None)

    async def get_level(self, resource_type: str, resource_id: str, user_id: str) -> str | None:
        g = self._g.get((resource_type, resource_id, user_id))
        return g.level if g is not None else None

    async def list_grants(self, resource_type: str, resource_id: str) -> list[ResourceGrant]:
        return [deepcopy(g) for k, g in self._g.items() if k[0] == resource_type and k[1] == resource_id]
