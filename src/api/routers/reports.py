from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from src.api.auth import get_current_user
from src.api.dependencies import get_report_service
from src.domain.user import User
from src.services.report_service import ReportService

router = APIRouter(prefix="/reports", tags=["reports"])


class CreateReportRequest(BaseModel):
    title: str
    abstract: str | None = None


class UpdateReportRequest(BaseModel):
    title: str | None = None
    abstract: str | None = None
    visibility: str | None = None


class CreateSectionRequest(BaseModel):
    content_json: dict[str, Any] = {}


class UpdateSectionRequest(BaseModel):
    content_json: dict[str, Any] = {}


@router.post("", status_code=201)
async def create_report(
    body: CreateReportRequest,
    user: User = Depends(get_current_user),
    svc: ReportService = Depends(get_report_service),
) -> dict:
    report = await svc.create(user.id, body.title, body.abstract)
    return asdict(report)


@router.get("")
async def list_reports(
    scope: str = Query("mine", pattern="^(mine|public)$"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    svc: ReportService = Depends(get_report_service),
) -> list[dict]:
    if scope == "public":
        reports = await svc.list_public(limit, offset)
    else:
        reports = await svc.list_my_reports(user.id, limit, offset)
    return [asdict(r) for r in reports]


@router.get("/{report_id}")
async def get_report(
    report_id: str,
    user: User = Depends(get_current_user),
    svc: ReportService = Depends(get_report_service),
) -> dict:
    report = await svc.get(user.id, report_id)
    return asdict(report)


@router.put("/{report_id}")
async def update_report(
    report_id: str,
    body: UpdateReportRequest,
    user: User = Depends(get_current_user),
    svc: ReportService = Depends(get_report_service),
) -> dict:
    report = await svc.update(user.id, report_id, body.title, body.abstract, body.visibility)
    return asdict(report)


@router.delete("/{report_id}", status_code=204)
async def delete_report(
    report_id: str,
    user: User = Depends(get_current_user),
    svc: ReportService = Depends(get_report_service),
) -> None:
    await svc.delete(user.id, report_id)


@router.post("/{report_id}/sections", status_code=201)
async def add_section(
    report_id: str,
    body: CreateSectionRequest,
    user: User = Depends(get_current_user),
    svc: ReportService = Depends(get_report_service),
) -> dict:
    section = await svc.add_section(user.id, report_id, body.content_json)
    return asdict(section)


@router.put("/{report_id}/sections/{section_id}")
async def update_section(
    report_id: str,
    section_id: str,
    body: UpdateSectionRequest,
    user: User = Depends(get_current_user),
    svc: ReportService = Depends(get_report_service),
) -> dict:
    section = await svc.edit_section(user.id, section_id, body.content_json)
    return asdict(section)


@router.delete("/{report_id}/sections/{section_id}", status_code=204)
async def delete_section(
    report_id: str,
    section_id: str,
    user: User = Depends(get_current_user),
    svc: ReportService = Depends(get_report_service),
) -> None:
    await svc.delete_section(user.id, section_id)


@router.post("/{report_id}/sections/{section_id}/lock")
async def acquire_lock(
    report_id: str,
    section_id: str,
    user: User = Depends(get_current_user),
    svc: ReportService = Depends(get_report_service),
) -> dict:
    acquired = await svc.acquire_lock(user.id, section_id)
    return {"acquired": acquired}


@router.delete("/{report_id}/sections/{section_id}/lock", status_code=204)
async def release_lock(
    report_id: str,
    section_id: str,
    user: User = Depends(get_current_user),
    svc: ReportService = Depends(get_report_service),
) -> None:
    await svc.release_lock(user.id, section_id)


@router.get("/{report_id}/sections/{section_id}/versions")
async def list_versions(
    report_id: str,
    section_id: str,
    limit: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
    svc: ReportService = Depends(get_report_service),
) -> list[dict]:
    versions = await svc._reports.get_versions(section_id, limit)
    return [asdict(v) for v in versions]
