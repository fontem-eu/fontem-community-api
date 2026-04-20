from __future__ import annotations

from dishka.integrations.fastapi import FromDishka, inject

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, Query, UploadFile, File, HTTPException
from pydantic import BaseModel

from src.api.auth import get_current_user, get_optional_user
from src.domain.user import User
from src.infra.minio_client import MinioStorage, ALLOWED_TYPES, MAX_SIZE
from src.services.report_service import ReportService

router = APIRouter(prefix="/reports", tags=["reports"])


class CreateReportRequest(BaseModel):
    title: str
    abstract: str | None = None
    parent_id: str | None = None  # for dossier nesting


class UpdateReportRequest(BaseModel):
    title: str | None = None
    abstract: str | None = None
    visibility: str | None = None


class CreateSectionRequest(BaseModel):
    content: str = ""


class UpdateSectionRequest(BaseModel):
    content: str = ""


class SaveDocumentRequest(BaseModel):
    """Save the full TipTap JSON document (v2 format)."""
    tiptap: dict
    version: int = 2


@router.post("", status_code=201)
@inject
async def create_report(
    body: CreateReportRequest,
    *,
    svc: FromDishka[ReportService],
    user: User = Depends(get_current_user),
) -> dict:
    report = await svc.create(user.id, body.title, body.abstract, body.parent_id)
    return asdict(report)


@router.get("")
@inject
async def list_reports(
    scope: str = Query("mine", pattern="^(mine|public)$"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    *,
    svc: FromDishka[ReportService],
    user: User | None = Depends(get_optional_user),
) -> list[dict]:
    # scope=public is browseable anonymously — the feed is the platform's
    # transparency surface. Anonymous callers only see `public_open`
    # reports; signed-in callers also see `public_auth` ones.
    if scope == "public":
        reports = await svc.list_public(limit, offset, authenticated=user is not None)
        return [asdict(r) for r in reports]
    # scope=mine requires auth — re-raise the 401 the optional dep swallowed.
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    reports = await svc.list_my_reports(user.id, limit, offset)
    return [asdict(r) for r in reports]


@router.get("/{report_id}")
@inject
async def get_report(
    report_id: str,
    *,
    svc: FromDishka[ReportService],
    user: User = Depends(get_current_user),
) -> dict:
    report = await svc.get(user.id, report_id)
    result = asdict(report)
    sections = await svc.get_sections(report_id)
    # v2 reports store a single section with TipTap JSON
    if sections and sections[0].content_json.get("version") == 2:
        result["content_doc"] = sections[0].content_json
        result["sections"] = []
    else:
        result["sections"] = [
            {**asdict(s), "content": s.content_json.get("html", "")}
            for s in sections
        ]
    # Include child reports for dossier tree navigation
    children = await svc.list_children(report_id)
    result["children"] = [{"id": c.id, "title": c.title} for c in children]
    return result


@router.put("/{report_id}")
@inject
async def update_report(
    report_id: str,
    body: UpdateReportRequest,
    *,
    svc: FromDishka[ReportService],
    user: User = Depends(get_current_user),
) -> dict:
    report = await svc.update(user.id, report_id, body.title, body.abstract, body.visibility)
    return asdict(report)


@router.delete("/{report_id}", status_code=204)
@inject
async def delete_report(
    report_id: str,
    *,
    svc: FromDishka[ReportService],
    user: User = Depends(get_current_user),
) -> None:
    await svc.delete(user.id, report_id)


@router.post("/{report_id}/sections", status_code=201)
@inject
async def add_section(
    report_id: str,
    body: CreateSectionRequest,
    *,
    svc: FromDishka[ReportService],
    user: User = Depends(get_current_user),
) -> dict:
    section = await svc.add_section(user.id, report_id, {"html": body.content})
    result = asdict(section)
    result["content"] = result.get("content_json", {}).get("html", "")
    return result


@router.put("/{report_id}/sections/{section_id}")
@inject
async def update_section(
    report_id: str,
    section_id: str,
    body: UpdateSectionRequest,
    *,
    svc: FromDishka[ReportService],
    user: User = Depends(get_current_user),
) -> dict:
    section = await svc.edit_section(user.id, section_id, {"html": body.content})
    result = asdict(section)
    result["content"] = result.get("content_json", {}).get("html", "")
    return result


@router.delete("/{report_id}/sections/{section_id}", status_code=204)
@inject
async def delete_section(
    report_id: str,
    section_id: str,
    *,
    svc: FromDishka[ReportService],
    user: User = Depends(get_current_user),
) -> None:
    await svc.delete_section(user.id, section_id)


@router.post("/{report_id}/sections/{section_id}/lock")
@inject
async def acquire_lock(
    report_id: str,
    section_id: str,
    *,
    svc: FromDishka[ReportService],
    user: User = Depends(get_current_user),
) -> dict:
    acquired = await svc.acquire_lock(user.id, section_id)
    return {"acquired": acquired}


@router.delete("/{report_id}/sections/{section_id}/lock", status_code=204)
@inject
async def release_lock(
    report_id: str,
    section_id: str,
    *,
    svc: FromDishka[ReportService],
    user: User = Depends(get_current_user),
) -> None:
    await svc.release_lock(user.id, section_id)


@router.get("/{report_id}/sections/{section_id}/versions")
@inject
async def list_versions(
    report_id: str,
    section_id: str,
    limit: int = Query(20, ge=1, le=100),
    *,
    svc: FromDishka[ReportService],
    user: User = Depends(get_current_user),
) -> list[dict]:
    versions = await svc._reports.get_versions(section_id, limit)
    return [asdict(v) for v in versions]


# ── v2 Document API ──────────────────────────────────────────

@router.put("/{report_id}/content")
@inject
async def save_document(
    report_id: str,
    body: SaveDocumentRequest,
    *,
    svc: FromDishka[ReportService],
    user: User = Depends(get_current_user),
) -> dict:
    """Save the full report document as TipTap JSON (v2 format)."""
    await svc.save_document(user.id, report_id, {
        "tiptap": body.tiptap,
        "version": body.version,
    })
    return {"ok": True}


# ── Image Upload ─────────────────────────────────────────────

_storage = None


def _get_storage() -> MinioStorage:
    global _storage
    if _storage is None:
        _storage = MinioStorage()
    return _storage


@router.post("/{report_id}/upload")
@inject
async def upload_image(
    report_id: str,
    file: UploadFile = File(...),
    *,
    svc: FromDishka[ReportService],
    user: User = Depends(get_current_user),
) -> dict:
    """Upload an image to attach to a report."""
    await svc._perms.require(user.id, report_id, "editor")

    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(400, f"File type {file.content_type} not allowed. Use: {', '.join(ALLOWED_TYPES)}")

    data = await file.read()
    if len(data) > MAX_SIZE:
        raise HTTPException(400, f"File too large. Maximum {MAX_SIZE // 1024 // 1024}MB.")

    storage = _get_storage()
    key = storage.upload(report_id, data, file.content_type)
    return {"url": storage.get_url(key), "key": key}
