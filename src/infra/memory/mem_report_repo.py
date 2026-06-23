from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from src.domain.report import Report, Section, SectionVersion
from src.repositories.report_repository import ReportRepository


class InMemoryReportRepository(ReportRepository):
    def __init__(self) -> None:
        self._reports: dict[str, Report] = {}
        self._sections: dict[str, Section] = {}
        self._versions: dict[str, list[SectionVersion]] = {}
        self._tags: dict[str, list[str]] = {}

    async def create(self, report: Report) -> Report:
        if report.id is None:
            report.id = str(uuid4())
        now = datetime.now(timezone.utc)
        report.created_at = report.created_at or now
        report.updated_at = report.updated_at or now
        self._reports[report.id] = deepcopy(report)
        return deepcopy(report)

    async def get_by_id(self, report_id: str) -> Report | None:
        report = self._reports.get(report_id)
        return deepcopy(report) if report else None

    async def update(self, report: Report) -> Report:
        report.updated_at = datetime.now(timezone.utc)
        self._reports[report.id] = deepcopy(report)
        return deepcopy(report)

    async def delete(self, report_id: str) -> None:
        self._reports.pop(report_id, None)
        self._tags.pop(report_id, None)
        to_remove = [sid for sid, s in self._sections.items() if s.report_id == report_id]
        for sid in to_remove:
            self._sections.pop(sid, None)
            self._versions.pop(sid, None)

    async def list_for_user(self, user_id: str, limit: int, offset: int) -> list[Report]:
        results = [deepcopy(r) for r in self._reports.values() if r.created_by == user_id]
        results.sort(key=lambda r: r.updated_at or r.created_at or datetime.min, reverse=True)
        return results[offset : offset + limit]

    async def list_public(
        self, limit: int, offset: int, authenticated: bool = False,
        tag: str | None = None,
    ) -> list[Report]:
        allowed = ("public_open",) if not authenticated else ("public_open", "public_auth")
        results = [
            deepcopy(r)
            for r in self._reports.values()
            if r.visibility in allowed
            and (tag is None or tag in self._tags.get(r.id, []))
        ]
        results.sort(key=lambda r: r.updated_at or r.created_at or datetime.min, reverse=True)
        return results[offset : offset + limit]

    # ── Tags ──────────────────────────────────────────────────

    async def get_story_tags(self, report_id: str) -> list[str]:
        return sorted(self._tags.get(report_id, []))

    async def set_story_tags(self, report_id: str, tags: list[str]) -> None:
        # Plain replace; service layer enforces the cap + slug.
        self._tags[report_id] = list(tags)

    async def list_distinct_tags(self) -> list[tuple[str, int]]:
        # Public-only — match the Pg implementation so the in-memory
        # tests catch any "private tag leaked into the public chip
        # strip" regression.
        public_ids = {
            r.id for r in self._reports.values()
            if r.visibility in ("public_open", "public_auth")
        }
        counts: dict[str, int] = {}
        for rid, tags in self._tags.items():
            if rid not in public_ids:
                continue
            for t in tags:
                counts[t] = counts.get(t, 0) + 1
        # desc count, then alphabetical
        return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))

    async def add_section(self, report_id: str, section: Section) -> Section:
        if section.id is None:
            section.id = str(uuid4())
        section.report_id = report_id
        section.updated_at = datetime.now(timezone.utc)
        existing = [s for s in self._sections.values() if s.report_id == report_id]
        if section.sort_order == 0:
            section.sort_order = len(existing) + 1
        self._sections[section.id] = deepcopy(section)
        return deepcopy(section)

    async def update_section(self, section: Section) -> Section:
        section.updated_at = datetime.now(timezone.utc)
        self._sections[section.id] = deepcopy(section)
        return deepcopy(section)

    async def delete_section(self, section_id: str) -> None:
        self._sections.pop(section_id, None)
        self._versions.pop(section_id, None)

    async def get_section(self, section_id: str) -> Section | None:
        section = self._sections.get(section_id)
        return deepcopy(section) if section else None

    async def get_sections(self, report_id: str) -> list[Section]:
        results = [deepcopy(s) for s in self._sections.values() if s.report_id == report_id]
        results.sort(key=lambda s: s.sort_order)
        return results

    async def acquire_lock(self, section_id: str, user_id: str, ttl_seconds: int) -> bool:
        section = self._sections.get(section_id)
        if section is None:
            return False
        now = datetime.now(timezone.utc)
        if section.lock_holder is not None and section.lock_holder != user_id:
            if section.lock_expires is not None and section.lock_expires > now:
                return False
        section.lock_holder = user_id
        section.lock_expires = now + timedelta(seconds=ttl_seconds)
        return True

    async def release_lock(self, section_id: str, user_id: str) -> None:
        section = self._sections.get(section_id)
        if section is not None and section.lock_holder == user_id:
            section.lock_holder = None
            section.lock_expires = None

    async def get_lock_holder(self, section_id: str) -> str | None:
        section = self._sections.get(section_id)
        if section is None:
            return None
        now = datetime.now(timezone.utc)
        if section.lock_holder is not None:
            if section.lock_expires is not None and section.lock_expires <= now:
                section.lock_holder = None
                section.lock_expires = None
                return None
            return section.lock_holder
        return None

    async def save_version(self, section_id: str, content: dict, user_id: str) -> None:
        version = SectionVersion(
            id=str(uuid4()),
            section_id=section_id,
            content_json=deepcopy(content),
            saved_by=user_id,
            saved_at=datetime.now(timezone.utc),
        )
        self._versions.setdefault(section_id, []).append(version)

    async def get_versions(self, section_id: str, limit: int) -> list[SectionVersion]:
        versions = self._versions.get(section_id, [])
        sorted_v = sorted(versions, key=lambda v: v.saved_at or datetime.min, reverse=True)
        return [deepcopy(v) for v in sorted_v[:limit]]

    async def set_dossier(
        self, report_id: str, dossier_id: str | None, parent_id: str | None,
    ) -> None:
        r = self._reports.get(report_id)
        if r is not None:
            r.dossier_id = dossier_id
            r.parent_id = parent_id

    async def list_by_dossier(self, dossier_id: str) -> list[Report]:
        results = [deepcopy(r) for r in self._reports.values() if r.dossier_id == dossier_id]
        results.sort(key=lambda r: r.created_at or datetime.min)
        return results

    async def set_investigation(self, report_id: str, investigation_id: str | None) -> None:
        r = self._reports.get(report_id)
        if r is not None:
            r.investigation_id = investigation_id

    async def list_by_investigation(self, investigation_id: str) -> list[Report]:
        results = [deepcopy(r) for r in self._reports.values()
                   if r.investigation_id == investigation_id]
        results.sort(key=lambda r: r.created_at or datetime.min)
        return results

    async def list_children(self, parent_id: str) -> list[Report]:
        results = [deepcopy(r) for r in self._reports.values() if r.parent_id == parent_id]
        results.sort(key=lambda r: r.created_at or datetime.min)
        return results
