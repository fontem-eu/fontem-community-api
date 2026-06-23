from __future__ import annotations

from abc import ABC, abstractmethod

from src.domain.visualization import Visualization


class VisualizationRepository(ABC):
    @abstractmethod
    async def create(self, viz: Visualization) -> Visualization: ...

    @abstractmethod
    async def get_by_id(self, viz_id: str) -> Visualization | None: ...

    @abstractmethod
    async def update(self, viz: Visualization) -> Visualization: ...

    @abstractmethod
    async def delete(self, viz_id: str) -> None: ...

    @abstractmethod
    async def list_for_user(self, user_id: str) -> list[Visualization]: ...

    @abstractmethod
    async def list_by_investigation(self, investigation_id: str) -> list[Visualization]: ...

    @abstractmethod
    async def set_investigation(self, viz_id: str, investigation_id: str | None) -> None: ...
