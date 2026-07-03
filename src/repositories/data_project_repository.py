"""Abstract repository for Data Studio projects + their queries and plots."""
from __future__ import annotations

from abc import ABC, abstractmethod

from src.domain.data_project import DataPlot, DataProject, DataQuery


class DataProjectRepository(ABC):
    @abstractmethod
    async def create_project(self, project: DataProject) -> DataProject: ...

    @abstractmethod
    async def get_project(self, project_id: str) -> DataProject | None: ...

    @abstractmethod
    async def list_for_user(self, user_id: str) -> list[DataProject]: ...

    @abstractmethod
    async def list_by_investigation(self, investigation_id: str) -> list[DataProject]: ...

    @abstractmethod
    async def set_investigation(
        self, project_id: str, investigation_id: str | None
    ) -> None: ...

    @abstractmethod
    async def update_project(self, project: DataProject) -> DataProject: ...

    @abstractmethod
    async def delete_project(self, project_id: str) -> None: ...

    @abstractmethod
    async def add_query(self, query: DataQuery) -> DataQuery: ...

    @abstractmethod
    async def update_query(self, query: DataQuery) -> DataQuery: ...

    @abstractmethod
    async def delete_query(self, query_id: str) -> None: ...

    @abstractmethod
    async def add_plot(self, plot: DataPlot) -> DataPlot: ...

    @abstractmethod
    async def update_plot(self, plot: DataPlot) -> DataPlot: ...

    @abstractmethod
    async def delete_plot(self, plot_id: str) -> None: ...
