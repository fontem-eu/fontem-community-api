from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.report import Report, Section, SectionVersion
from src.infra.postgres.models import (
    ReportAccessModel,
    ReportModel,
    SectionModel,
    SectionVersionModel,
)
from src.repositories.report_repository import ReportRepository


class PgReportRepository(ReportRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── domain ↔ ORM helpers ──────────────────────────────────────

    @staticmethod
    def _report_to_domain(row: ReportModel) -> Report:
        return Report(
            id=row.id,
            title=row.title,
            abstract=row.abstract,
            visibility=row.visibility,
            created_by=row.created_by,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _section_to_domain(row: SectionModel) -> Section:
        return Section(
            id=row.id,
            report_id=row.report_id,
            sort_order=row.sort_order,
            content_json=row.content_json or {},
            lock_holder=row.lock_holder,
            lock_expires=row.lock_expires,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _version_to_domain(row: SectionVersionModel) -> SectionVersion:
        return SectionVersion(
            id=str(row.id),
            section_id=row.section_id,
            content_json=row.content_json or {},
            saved_by=row.saved_by,
            saved_at=row.saved_at,
        )

    # ── Report CRUD ───────────────────────────────────────────────

    async def create(self, report: Report) -> Report:
        now = datetime.now(timezone.utc)
        model = ReportModel(
            id=report.id or str(uuid4()),
            title=report.title,
            abstract=report.abstract,
            visibility=report.visibility,
            created_by=report.created_by,
            created_at=report.created_at or now,
            updated_at=report.updated_at or now,
        )
        self._session.add(model)
        await self._session.flush()
        return self._report_to_domain(model)

    async def get_by_id(self, report_id: str) -> Report | None:
        result = await self._session.execute(
            select(ReportModel).where(ReportModel.id == report_id)
        )
        row = result.scalar_one_or_none()
        return self._report_to_domain(row) if row else None

    async def update(self, report: Report) -> Report:
        result = await self._session.execute(
            select(ReportModel).where(ReportModel.id == report.id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise ValueError(f"Report {report.id} not found")
        row.title = report.title
        row.abstract = report.abstract
        row.visibility = report.visibility
        row.updated_at = datetime.now(timezone.utc)
        await self._session.flush()
        return self._report_to_domain(row)

    async def delete(self, report_id: str) -> None:
        await self._session.execute(
            delete(ReportModel).where(ReportModel.id == report_id)
        )
        await self._session.flush()

    async def list_for_user(self, user_id: str, limit: int, offset: int) -> list[Report]:
        # Reports where created_by=user OR user has report_access
        access_subq = (
            select(ReportAccessModel.report_id)
            .where(ReportAccessModel.user_id == user_id)
            .correlate(None)
        )
        stmt = (
            select(ReportModel)
            .where(
                (ReportModel.created_by == user_id)
                | (ReportModel.id.in_(access_subq))
            )
            .order_by(ReportModel.updated_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        return [self._report_to_domain(r) for r in result.scalars().all()]

    async def list_public(self, limit: int, offset: int) -> list[Report]:
        stmt = (
            select(ReportModel)
            .where(ReportModel.visibility.in_(["public_auth", "public_open"]))
            .order_by(ReportModel.updated_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        return [self._report_to_domain(r) for r in result.scalars().all()]

    # ── Section CRUD ──────────────────────────────────────────────

    async def add_section(self, report_id: str, section: Section) -> Section:
        now = datetime.now(timezone.utc)
        sort_order = section.sort_order
        if sort_order == 0:
            result = await self._session.execute(
                select(func.count())
                .select_from(SectionModel)
                .where(SectionModel.report_id == report_id)
            )
            count = result.scalar_one()
            sort_order = count + 1

        model = SectionModel(
            id=section.id or str(uuid4()),
            report_id=report_id,
            sort_order=sort_order,
            content_json=section.content_json or {},
            lock_holder=section.lock_holder,
            lock_expires=section.lock_expires,
            updated_at=now,
        )
        self._session.add(model)
        await self._session.flush()
        return self._section_to_domain(model)

    async def update_section(self, section: Section) -> Section:
        result = await self._session.execute(
            select(SectionModel).where(SectionModel.id == section.id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise ValueError(f"Section {section.id} not found")
        row.sort_order = section.sort_order
        row.content_json = section.content_json
        row.lock_holder = section.lock_holder
        row.lock_expires = section.lock_expires
        row.updated_at = datetime.now(timezone.utc)
        await self._session.flush()
        return self._section_to_domain(row)

    async def delete_section(self, section_id: str) -> None:
        await self._session.execute(
            delete(SectionModel).where(SectionModel.id == section_id)
        )
        await self._session.flush()

    async def get_section(self, section_id: str) -> Section | None:
        result = await self._session.execute(
            select(SectionModel).where(SectionModel.id == section_id)
        )
        row = result.scalar_one_or_none()
        return self._section_to_domain(row) if row else None

    async def get_sections(self, report_id: str) -> list[Section]:
        result = await self._session.execute(
            select(SectionModel)
            .where(SectionModel.report_id == report_id)
            .order_by(SectionModel.sort_order)
        )
        return [self._section_to_domain(r) for r in result.scalars().all()]

    # ── Locking ───────────────────────────────────────────────────

    async def acquire_lock(self, section_id: str, user_id: str, ttl_seconds: int) -> bool:
        result = await self._session.execute(
            select(SectionModel)
            .where(SectionModel.id == section_id)
            .with_for_update()
        )
        row = result.scalar_one_or_none()
        if row is None:
            return False
        now = datetime.now(timezone.utc)
        if row.lock_holder is not None and row.lock_holder != user_id:
            if row.lock_expires is not None and row.lock_expires > now:
                return False
        row.lock_holder = user_id
        row.lock_expires = now + timedelta(seconds=ttl_seconds)
        await self._session.flush()
        return True

    async def release_lock(self, section_id: str, user_id: str) -> None:
        result = await self._session.execute(
            select(SectionModel).where(SectionModel.id == section_id)
        )
        row = result.scalar_one_or_none()
        if row is not None and row.lock_holder == user_id:
            row.lock_holder = None
            row.lock_expires = None
            await self._session.flush()

    async def get_lock_holder(self, section_id: str) -> str | None:
        result = await self._session.execute(
            select(SectionModel).where(SectionModel.id == section_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        now = datetime.now(timezone.utc)
        if row.lock_holder is not None:
            if row.lock_expires is not None and row.lock_expires <= now:
                row.lock_holder = None
                row.lock_expires = None
                await self._session.flush()
                return None
            return row.lock_holder
        return None

    # ── Versioning ────────────────────────────────────────────────

    async def save_version(self, section_id: str, content: dict, user_id: str) -> None:
        model = SectionVersionModel(
            section_id=section_id,
            content_json=content,
            saved_by=user_id,
            saved_at=datetime.now(timezone.utc),
        )
        self._session.add(model)
        await self._session.flush()

    async def get_versions(self, section_id: str, limit: int) -> list[SectionVersion]:
        result = await self._session.execute(
            select(SectionVersionModel)
            .where(SectionVersionModel.section_id == section_id)
            .order_by(SectionVersionModel.saved_at.desc())
            .limit(limit)
        )
        return [self._version_to_domain(r) for r in result.scalars().all()]

    async def list_children(self, parent_id: str) -> list[Report]:
        result = await self._session.execute(
            select(ReportModel)
            .where(ReportModel.parent_id == parent_id)
            .order_by(ReportModel.created_at)
        )
        return [self._to_domain(r) for r in result.scalars().all()]
