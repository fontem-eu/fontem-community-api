from __future__ import annotations

from src.domain.report import Report, Section
from src.repositories.report_repository import ReportRepository
from src.services.exceptions import Conflict, NotFound
from src.services.permission_service import PermissionService
from src.services.sanitize import sanitize_html, sanitize_text

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
        if parent_id is not None:
            parent = await self._reports.get_by_id(parent_id)
            if parent is None:
                raise NotFound(f"Parent report {parent_id} not found")
        report = Report(
            title=sanitize_text(title),
            abstract=sanitize_text(abstract) if abstract else abstract,
            parent_id=parent_id,
            created_by=user_id,
        )
        report = await self._reports.create(report)
        await self._perms.grant_access(report.id, user_id, "owner")
        return report

    async def get(self, user_id: str, report_id: str) -> Report:
        await self._perms.require(user_id, report_id, "viewer")
        report = await self._reports.get_by_id(report_id)
        if report is None:
            raise NotFound(f"Report {report_id} not found")
        return report

    async def get_viewable(
        self, user_id: str | None, report_id: str,
    ) -> Report:
        """Fetch a report, honouring its visibility against an optional user.

        Anonymous callers (``user_id=None``) only see reports with
        visibility ``public_open``. Authenticated callers go through the
        regular permission check (which also honours ``public_auth``).

        Anonymous attempts to access non-public reports return 404 —
        don't leak whether a private report exists by giving a
        distinguishable 403 vs 404.
        """
        report = await self._reports.get_by_id(report_id)
        if report is None:
            raise NotFound(f"Report {report_id} not found")
        if user_id is None:
            if report.visibility != "public_open":
                raise NotFound(f"Report {report_id} not found")
        else:
            await self._perms.require(user_id, report_id, "viewer")
        return report

    # pylint: disable-next=too-many-arguments,too-many-positional-arguments
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
            report.title = sanitize_text(title)
        if abstract is not None:
            report.abstract = sanitize_text(abstract)
        if visibility is not None:
            report.visibility = visibility
        report = await self._reports.update(report)
        return report

    async def delete(self, user_id: str, report_id: str) -> None:
        await self._perms.require(user_id, report_id, "owner")
        await self._reports.delete(report_id)

    async def add_section(self, user_id: str, report_id: str, content: dict) -> Section:
        await self._perms.require(user_id, report_id, "editor")
        section = Section(content_json=_sanitize_section(content))
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
        section.content_json = _sanitize_section(content)
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

    async def list_public(
        self, limit: int, offset: int, authenticated: bool = False,
        tag: str | None = None,
    ) -> list[Report]:
        """List reports browseable by the caller.

        Anonymous callers see ``public_open`` only. Signed-in callers
        additionally see ``public_auth`` (reports meant for any signed-in
        user but not the broader public). When ``tag`` is given, the
        list is filtered to stories carrying that tag.
        """
        return await self._reports.list_public(
            limit, offset, authenticated=authenticated, tag=tag,
        )

    async def get_tags(self, report_id: str) -> list[str]:
        """Bulk-friendly read; the router uses this to embed tags in
        the GET-by-id payload + the carousel cards."""
        return await self._reports.get_story_tags(report_id)

    async def list_children(self, parent_id: str) -> list[Report]:
        """List child reports (dossier sub-pages)."""
        return await self._reports.list_children(parent_id)

    async def save_document(self, user_id: str, report_id: str, content: dict) -> None:
        """Save the entire report as a single v2 TipTap JSON document.

        Replaces all existing sections with one section containing the
        full document. Previous content is saved as a version snapshot.
        """
        await self._perms.require(user_id, report_id, "editor")
        sections = await self._reports.get_sections(report_id)
        if sections:
            # Save a version of the first section before overwriting
            await self._reports.save_version(sections[0].id, sections[0].content_json, user_id)
            # Update first section, delete the rest
            sections[0].content_json = content
            await self._reports.update_section(sections[0])
            for s in sections[1:]:
                await self._reports.delete_section(s.id)
        else:
            # No sections yet — create one
            section = Section(content_json=content)
            await self._reports.add_section(report_id, section)


def _sanitize_section(content: dict) -> dict:
    """Sanitize the HTML inside a section content dict."""
    if "html" in content:
        content = {**content, "html": sanitize_html(content["html"])}
    return content
