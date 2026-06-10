from __future__ import annotations

from dataclasses import asdict
from typing import Annotated, Literal

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from src.api.auth import get_current_user, get_optional_user
from src.api.openapi_responses import RESOURCE_RESPONSES, UuidPath, UuidStr
from src.domain.user import User
from src.infra.minio_client import MinioStorage
from src.services.file_security import scan_and_sanitise, make_clamd_client
from src.services.report_service import ReportService

# Mounted by app.py at both /data-stories (canonical) and /reports
# (legacy alias kept during the rename window). Routes inside this
# module use empty/relative paths so the prefix is supplied at include
# time.
router = APIRouter(tags=["data-stories"], responses=RESOURCE_RESPONSES)


class CreateReportRequest(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    abstract: str | None = Field(default=None, max_length=4000)
    # UUID-shaped or absent. Lifts the schema-vs-impl gap that made
    # fuzz tooling 400 on plain-string parent_id inputs and flag the
    # API behavior as "schema-compliant rejected".
    parent_id: UuidStr | None = None


class UpdateReportRequest(BaseModel):
    # `min_length=1` lived here briefly to mirror the create-time
    # constraint after the 2026-05-10 schemathesis pass tightened the
    # body shape. It was over-restrictive: the editor sends the full
    # tuple (title/abstract/visibility) on every save, and the smoke
    # suite hit 422 whenever the editor mounted into an empty-title
    # state for a frame before the test's page.fill landed. Empty
    # title isn't ambiguous semantically (it just means "title is
    # empty"), so accept it here and keep the cap to bound DB writes.
    title: str | None = Field(default=None, max_length=300)
    abstract: str | None = Field(default=None, max_length=4000)
    visibility: Literal["private", "public_open", "public_auth"] | None = None


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
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    report = await svc.create(user.id, body.title, body.abstract, body.parent_id)
    return asdict(report)


# Anonymous browsing of `scope=public` is intentional — the public
# feed is the platform's transparency surface. Tell schemathesis (and
# any other OpenAPI-driven client) that this operation has no security
# requirement; otherwise it gets flagged as "API accepts requests
# without authentication". The handler still consumes get_optional_user
# so signed-in callers see public_auth stories on top of public_open.
@router.get(
    "",
    openapi_extra={"security": []},
    responses={401: {"description": "Authentication required when scope=mine."}},
)
@inject
# scope/limit/offset/tag are the feed's filtering surface; collapsing
# them into one request-body BaseModel would just push the same names
# into a wrapper for no readability gain on the call sites (curl/HTTPie).
# pylint: disable-next=too-many-arguments,too-many-positional-arguments
async def list_reports(
    *,
    scope: Annotated[str, Query(pattern="^(mine|public)$")] = "mine",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=2**31 - 1)] = 0,
    tag: Annotated[str | None, Query(max_length=40)] = None,
    svc: FromDishka[ReportService],
    user: Annotated[User | None, Depends(get_optional_user)],
) -> list[dict]:
    # scope=public is browseable anonymously — the feed is the platform's
    # transparency surface. Anonymous callers only see `public_open`
    # reports; signed-in callers also see `public_auth` ones.
    if scope == "public":
        reports = await svc.list_public(
            limit, offset,
            authenticated=user is not None,
            tag=tag,
        )
        # Embed tags inline so the feed cards can render the tag pills
        # without N+1 round-trips. Cheap — at most 3 per story.
        out = []
        for r in reports:
            d = asdict(r)
            d["tags"] = await svc.get_tags(r.id)
            out.append(d)
        return out
    # scope=mine requires auth — re-raise the 401 the optional dep swallowed.
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    reports = await svc.list_my_reports(user.id, limit, offset)
    return [asdict(r) for r in reports]


# public_open reports are readable anonymously; mark the operation as
# unauthenticated in the OpenAPI spec so schemathesis stops flagging
# the 200 responses as "accepts requests without authentication".
@router.get("/{report_id}", openapi_extra={"security": []})
@inject
async def get_report(
    report_id: UuidPath,
    *,
    svc: FromDishka[ReportService],
    user: Annotated[User | None, Depends(get_optional_user)],
) -> dict:
    # Anonymous visitors can read `public_open` reports — that's the
    # point of having them. The service layer enforces visibility vs
    # user_id (None for anonymous) and 404s on any non-public attempt
    # so we don't leak the existence of private reports.
    uid = user.id if user is not None else None
    report = await svc.get_viewable(uid, report_id)
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
    # Tag pills on the story page render from this; the same payload
    # also seeds the editor when the owner edits tags.
    result["tags"] = await svc.get_tags(report_id)
    return result


@router.put("/{report_id}")
@inject
async def update_report(
    report_id: UuidPath,
    body: UpdateReportRequest,
    *,
    svc: FromDishka[ReportService],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    report = await svc.update(user.id, report_id, body.title, body.abstract, body.visibility)
    return asdict(report)


@router.delete("/{report_id}", status_code=204)
@inject
async def delete_report(
    report_id: UuidPath,
    *,
    svc: FromDishka[ReportService],
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    await svc.delete(user.id, report_id)


@router.post("/{report_id}/sections", status_code=201)
@inject
async def add_section(
    report_id: UuidPath,
    body: CreateSectionRequest,
    *,
    svc: FromDishka[ReportService],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    section = await svc.add_section(user.id, report_id, {"html": body.content})
    result = asdict(section)
    result["content"] = result.get("content_json", {}).get("html", "")
    return result


# ``report_id`` on the sub-section routes is part of the URL hierarchy
# (it scopes the route under the report) but the service-layer call
# only needs ``section_id`` — the perms gate is keyed on the section's
# report. The path parameter still has to be named ``report_id`` so
# FastAPI binds the placeholder; pylint just can't see that.
@router.put("/{report_id}/sections/{section_id}")
@inject
async def update_section(
    report_id: UuidPath,  # pylint: disable=unused-argument
    section_id: UuidPath,
    body: UpdateSectionRequest,
    *,
    svc: FromDishka[ReportService],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    section = await svc.edit_section(user.id, section_id, {"html": body.content})
    result = asdict(section)
    result["content"] = result.get("content_json", {}).get("html", "")
    return result


@router.delete("/{report_id}/sections/{section_id}", status_code=204)
@inject
async def delete_section(
    report_id: UuidPath,  # pylint: disable=unused-argument
    section_id: UuidPath,
    *,
    svc: FromDishka[ReportService],
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    await svc.delete_section(user.id, section_id)


@router.post("/{report_id}/sections/{section_id}/lock")
@inject
async def acquire_lock(
    report_id: UuidPath,  # pylint: disable=unused-argument
    section_id: UuidPath,
    *,
    svc: FromDishka[ReportService],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    acquired = await svc.acquire_lock(user.id, section_id)
    return {"acquired": acquired}


@router.delete("/{report_id}/sections/{section_id}/lock", status_code=204)
@inject
async def release_lock(
    report_id: UuidPath,  # pylint: disable=unused-argument
    section_id: UuidPath,
    *,
    svc: FromDishka[ReportService],
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    await svc.release_lock(user.id, section_id)


@router.get("/{report_id}/sections/{section_id}/versions")
@inject
async def list_versions(
    report_id: UuidPath,  # pylint: disable=unused-argument
    section_id: UuidPath,
    *,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    svc: FromDishka[ReportService],
    _user: Annotated[User, Depends(get_current_user)],
) -> list[dict]:
    # Version-list is a thin pass-through: there's no service-layer
    # business logic on top, just the repo query. Reaching through to
    # the canonical repo handle is the cleanest seam.
    versions = await svc._reports.get_versions(section_id, limit)  # pylint: disable=protected-access
    return [asdict(v) for v in versions]


# ── v2 Document API ──────────────────────────────────────────

@router.put("/{report_id}/content")
@inject
async def save_document(
    report_id: UuidPath,
    body: SaveDocumentRequest,
    *,
    svc: FromDishka[ReportService],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Save the full report document as TipTap JSON (v2 format)."""
    await svc.save_document(user.id, report_id, {
        "tiptap": body.tiptap,
        "version": body.version,
    })
    return {"ok": True}


# ── Image Upload ─────────────────────────────────────────────

# Lazily-constructed MinIO client. Singleton because the underlying
# httpx pool is expensive to spin up per request, and the credentials
# come from env vars at process start. Lowercase by intent — module
# state, not a constant. The pylint "constant naming style" check is
# specifically for *constants*; this is a cache slot, so disable it.
_storage: MinioStorage | None = None  # pylint: disable=invalid-name
# Module-scoped cache. None means "not yet initialised"; the actual
# initialised value can also be None when CLAMAV_HOST is unset (dev
# runs), so we use a sentinel object to distinguish the two states.
_CLAMD_SENTINEL = object()
_clamd_client = _CLAMD_SENTINEL  # pylint: disable=invalid-name


def _get_storage() -> MinioStorage:
    global _storage  # pylint: disable=global-statement
    if _storage is None:
        _storage = MinioStorage()
    return _storage


def _get_clamd():
    """Singleton lazy clamd client; returns None when no env is set
    (dev / in-memory tests). See file_security.make_clamd_client."""
    global _clamd_client  # pylint: disable=global-statement
    if _clamd_client is _CLAMD_SENTINEL:
        _clamd_client = make_clamd_client()
    return _clamd_client


@router.post(
    "/{report_id}/upload",
    responses={
        400: {
            "description": (
                "Upload rejected — unsupported file type, size over the "
                "cap, image dimensions over the cap, structurally "
                "invalid raster, malformed SVG, or AV signature hit."
            ),
        },
    },
)
@inject
async def upload_image(
    report_id: UuidPath,
    *,
    file: Annotated[UploadFile, File(...)],
    svc: FromDishka[ReportService],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Upload an image or SVG attachment to a report.

    Goes through the file_security pipeline (magic-byte sniff →
    raster re-encode or SVG sanitise → clamd INSTREAM). The bytes
    that reach MinIO are the cleaned, post-sanitisation payload —
    never the raw client upload. ``file.content_type`` is treated as
    untrusted; the canonical MIME comes back from the pipeline.
    """
    # Permission check delegates to the canonical PermissionService
    # owned by ReportService — there's no top-level service for raw
    # ACL questions, so we reach through. Documented seam.
    await svc._perms.require(user.id, report_id, "editor")  # pylint: disable=protected-access

    raw = await file.read()
    # InvalidInput from the pipeline propagates to the app-level
    # handler → 400. No try/except here.
    cleaned = scan_and_sanitise(raw, clamd_client=_get_clamd())

    storage = _get_storage()
    key = storage.upload(report_id, cleaned.data, cleaned.content_type)
    return {"url": storage.get_url(key), "key": key}
