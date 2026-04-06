from __future__ import annotations

from abc import ABC, abstractmethod

from src.domain.report import Report, Section, SectionVersion


class ReportRepository(ABC):
    @abstractmethod
    async def create(self, report: Report) -> Report: ...

    @abstractmethod
    async def get_by_id(self, report_id: str) -> Report | None: ...

    @abstractmethod
    async def update(self, report: Report) -> Report: ...

    @abstractmethod
    async def delete(self, report_id: str) -> None: ...

    @abstractmethod
    async def list_for_user(self, user_id: str, limit: int, offset: int) -> list[Report]: ...

    @abstractmethod
    async def list_public(self, limit: int, offset: int) -> list[Report]: ...

    @abstractmethod
    async def add_section(self, report_id: str, section: Section) -> Section: ...

    @abstractmethod
    async def update_section(self, section: Section) -> Section: ...

    @abstractmethod
    async def delete_section(self, section_id: str) -> None: ...

    @abstractmethod
    async def get_section(self, section_id: str) -> Section | None: ...

    @abstractmethod
    async def get_sections(self, report_id: str) -> list[Section]: ...

    @abstractmethod
    async def acquire_lock(self, section_id: str, user_id: str, ttl_seconds: int) -> bool: ...

    @abstractmethod
    async def release_lock(self, section_id: str, user_id: str) -> None: ...

    @abstractmethod
    async def get_lock_holder(self, section_id: str) -> str | None: ...

    @abstractmethod
    async def save_version(self, section_id: str, content: dict, user_id: str) -> None: ...

    @abstractmethod
    async def get_versions(self, section_id: str, limit: int) -> list[SectionVersion]: ...

    @abstractmethod
    async def list_children(self, parent_id: str) -> list[Report]: ...
