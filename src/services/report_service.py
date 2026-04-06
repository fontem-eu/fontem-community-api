from __future__ import annotations

from src.domain.report import Report, Section
from src.repositories.report_repository import ReportRepository
from src.services.exceptions import Conflict, NotFound
from src.services.permission_service import PermissionService

DEFAULT_LOCK_TTL = 300  # 5 minutes


class ReportService:
    def __init__(
        self,
        reports: ReportRepository,
        perms: PermissionService,
    ) -> None:
        self._reports = reports
        self._perms = perms

    async def create(
        self, user_id: str, title: str,
        abstract: str | None = None,
        parent_id: str | None = None,
    ) -> Report:
        report = Report(title=title, abstract=abstract, parent_id=parent_id, created_by=user_id)
        report = await self._reports.create(report)
        await self._perms.grant_access(report.id, user_id, "owner")
        return report

    async def get(self, user_id: str, report_id: str) -> Report:
        await self._perms.require(user_id, report_id, "viewer")
        report = await self._reports.get_by_id(report_id)
        if report is None:
            raise NotFound(f"Report {report_id} not found")
        return report

    async def update(
        self,
        user_id: str,
        report_id: str,
        title: str | None = None,
        abstract: str | None = None,
        visibility: str | None = None,
    ) -> Report:
        await self._perms.require(user_id, report_id, "owner")
        report = await self._reports.get_by_id(report_id)
        if report is None:
            raise NotFound(f"Report {report_id} not found")
        if title is not None:
            report.title = title
        if abstract is not None:
            report.abstract = abstract
        if visibility is not None:
            report.visibility = visibility
        report = await self._reports.update(report)
        return report

    async def delete(self, user_id: str, report_id: str) -> None:
        await self._perms.require(user_id, report_id, "owner")
        await self._reports.delete(report_id)

    async def add_section(self, user_id: str, report_id: str, content: dict) -> Section:
        await self._perms.require(user_id, report_id, "editor")
        section = Section(content_json=content)
        return await self._reports.add_section(report_id, section)

    async def edit_section(self, user_id: str, section_id: str, content: dict) -> Section:
        section = await self._reports.get_section(section_id)
        if section is None:
            raise NotFound(f"Section {section_id} not found")
        await self._perms.require(user_id, section.report_id, "editor")
        # Lock check
        holder = await self._reports.get_lock_holder(section_id)
        if holder is not None and holder != user_id:
            raise Conflict(f"Section {section_id} is locked by {holder}")
        # Save version before editing
        await self._reports.save_version(section_id, section.content_json, user_id)
        section.content_json = content
        return await self._reports.update_section(section)

    async def delete_section(self, user_id: str, section_id: str) -> None:
        section = await self._reports.get_section(section_id)
        if section is None:
            raise NotFound(f"Section {section_id} not found")
        await self._perms.require(user_id, section.report_id, "editor")
        await self._reports.delete_section(section_id)

    async def acquire_lock(self, user_id: str, section_id: str) -> bool:
        section = await self._reports.get_section(section_id)
        if section is None:
            raise NotFound(f"Section {section_id} not found")
        await self._perms.require(user_id, section.report_id, "editor")
        return await self._reports.acquire_lock(section_id, user_id, DEFAULT_LOCK_TTL)

    async def release_lock(self, user_id: str, section_id: str) -> None:
        await self._reports.release_lock(section_id, user_id)

    async def get_sections(self, report_id: str) -> list[Section]:
        return await self._reports.get_sections(report_id)

    async def list_my_reports(self, user_id: str, limit: int, offset: int) -> list[Report]:
        return await self._reports.list_for_user(user_id, limit, offset)

    async def list_public(self, limit: int, offset: int) -> list[Report]:
        return await self._reports.list_public(limit, offset)

    async def list_children(self, parent_id: str) -> list[Report]:
        """List child reports (dossier sub-pages)."""
        return await self._reports.list_children(parent_id)
