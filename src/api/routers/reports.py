from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime
from typing import Annotated, Literal

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, Depends, File, HTTPException, Path, Query, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from src.api.auth import get_current_user, get_optional_user
from src.api.openapi_responses import RESOURCE_RESPONSES, UuidPath, UuidStr
from src.domain.user import User
from src.infra.minio_client import MinioStorage
from src.services.file_security import scan_and_sanitise, make_clamd_client
from src.services.report_service import ReportService
from src.services.upload_urls import presign_uploads

# Mounted by app.py at both /data-stories (canonical) and /reports
# (legacy alias kept during the rename window). Routes inside this
# module use empty/relative paths so the prefix is supplied at include
# time.
router = APIRouter(tags=["data-stories"], responses=RESOURCE_RESPONSES)


class ReportResponse(BaseModel):
    """Contract surface of a data story.

    ``extra="allow"``: handlers enrich the payload (sections, content_doc,
    tags, translation overlays), and the enrichments may evolve without a
    contract break. What is DECLARED here is what API consumers (and the
    pact ↔ spec validation) may rely on.
    """

    model_config = ConfigDict(extra="allow")

    id: str
    title: str
    abstract: str | None = None
    visibility: str
    language: str
    content_version: int
    created_by: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ReportSearchItem(ReportResponse):
    """Search results additionally carry the story's tags."""

    tags: list[str] = []


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
    # Optional NUTS region this story is about (any level) or "" to clear.
    nuts_region: str | None = Field(default=None, max_length=5, pattern=r"^([A-Za-z]{2}[A-Za-z0-9]{0,3})?$")
    language: str | None = Field(default=None, pattern="^[a-z]{2}$")


class SaveDocumentRequest(BaseModel):
    """Save the full TipTap JSON document (v2 format).

    ``base_revision`` is the revision the editor loaded. It is what lets
    the server tell a fresh save from one built on a stale buffer; a save
    that does not name its baseline is refused rather than guessed at.
    ``version`` is the document FORMAT, which is a different thing
    entirely and has been mistaken for a concurrency token before.
    """
    tiptap: dict
    version: int = 2
    base_revision: str | None = None


class SaveTranslationRequest(BaseModel):
    """Create/replace one language's translation of a story."""
    title: str = Field(default="", max_length=300)
    abstract: str | None = Field(default=None, max_length=4000)
    tiptap: dict
    version: int = 2


# path param for the translation endpoints; mirrors the service check so
# swagger-driven fuzzing gets a 422 (schema) rather than a 400 (impl).
LangPath = Annotated[str, Path(pattern="^[a-z]{2}$")]


@router.post("", status_code=201, response_model=ReportResponse)
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
def _apply_translation(d: dict, t: dict | None, lang: str | None) -> None:
    """Swap a feed card's title/abstract for the reader's language. The
    original text stays available under original_title so the UI can
    disambiguate, and translation_* fields let it badge outdated ones."""
    if not t:
        return
    d["original_title"] = d["title"]
    d["title"] = t["title"] or d["title"]
    if t["abstract"]:
        d["abstract"] = t["abstract"]
    d["translation_lang"] = lang
    d["translation_outdated"] = t["outdated"]


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
    lang: Annotated[str | None, Query(pattern="^[a-z]{2}$")] = None,
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
        overlay = await svc.translation_overlay(reports, lang)
        out = []
        for r in reports:
            d = asdict(r)
            d["tags"] = await svc.get_tags(r.id)
            _apply_translation(d, overlay.get(r.id), lang)
            out.append(d)
        return out
    # scope=mine requires auth — re-raise the 401 the optional dep swallowed.
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    reports = await svc.list_my_reports(user.id, limit, offset)
    overlay = await svc.translation_overlay(reports, lang)
    out = []
    for r in reports:
        d = asdict(r)
        _apply_translation(d, overlay.get(r.id), lang)
        out.append(d)
    return out


# Declared before /{report_id} so the literal "search" segment routes here
# rather than being parsed as a (non-UUID) report id.
@router.get("/search", openapi_extra={"security": []}, response_model=list[ReportSearchItem])
@inject
# pylint: disable-next=too-many-arguments,too-many-positional-arguments
async def search_reports(
    *,
    q: Annotated[str, Query(min_length=1, max_length=200)],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    offset: Annotated[int, Query(ge=0, le=2**31 - 1)] = 0,
    # `date`, not a shape-only regex. The pattern was r"^\d{4}-\d{2}-\d{2}$",
    # which accepts digit-shaped non-dates: "0000-00-00" passed validation,
    # reached datetime.strptime in ReportService._day_start, raised
    # ValueError and surfaced as a 500. Found by the Schemathesis fuzz in
    # the DAST run. Pydantic rejects an impossible date with 422 here, so
    # the service never sees one.
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
    lang: Annotated[str | None, Query(pattern="^[a-z]{2}$")] = None,
    svc: FromDishka[ReportService],
    user: Annotated[User | None, Depends(get_optional_user)],
) -> list[dict]:
    """Keyword search over public data stories (title + abstract).

    Anonymous callers see ``public_open`` only; signed-in callers also see
    ``public_auth`` — the same visibility gate as the public feed, so search
    never surfaces a private story. Backs the data-stories section of the
    unified /search results page.
    """
    reports = await svc.search_public(
        q, limit, offset,
        authenticated=user is not None,
        # The service takes ISO strings; hand it back the canonical form.
        date_from=date_from.isoformat() if date_from else None,
        date_to=date_to.isoformat() if date_to else None,
    )
    overlay = await svc.translation_overlay(reports, lang)
    out = []
    for r in reports:
        d = asdict(r)
        d["tags"] = await svc.get_tags(r.id)
        _apply_translation(d, overlay.get(r.id), lang)
        out.append(d)
    return out


# public_open reports are readable anonymously; mark the operation as
# unauthenticated in the OpenAPI spec so schemathesis stops flagging
# the 200 responses as "accepts requests without authentication".
@router.get("/{report_id}", openapi_extra={"security": []}, response_model=ReportResponse)
@inject
async def get_report(
    report_id: UuidPath,
    *,
    svc: FromDishka[ReportService],
    storage: FromDishka[MinioStorage],
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
    # The revision this payload is: the editor sends it back on save,
    # and that is what makes a stale overwrite detectable.
    head = await svc.document_head(report_id)
    result["head_revision"] = head.id if head else None
    # An editor edits their own draft, not the published text. Readers
    # never see this key, and an editor without a draft yet gets null —
    # their first save starts one off the published head.
    if uid:
        draft = await svc.draft_head(uid, report_id)
        if draft is not None and draft.id != result["head_revision"]:
            result["draft_revision"] = draft.id
            result["draft_doc"] = draft.content_json
    # Include child reports for dossier tree navigation
    children = await svc.list_children(report_id)
    result["children"] = [{"id": c.id, "title": c.title} for c in children]
    # Tag pills on the story page render from this; the same payload
    # also seeds the editor when the owner edits tags.
    result["tags"] = await svc.get_tags(report_id)
    # Language switcher on the story page renders from this summary —
    # full translated bodies are fetched lazily per language.
    _, tmeta = await svc.list_translations(uid, report_id)
    result["translations"] = [
        {"lang": t["lang"], "outdated": t["outdated"]} for t in tmeta
    ]
    # Rewrite every `/uploads/<key>` reference in the payload to a
    # freshly-signed URL. Authz has already cleared the read; this is
    # purely the URL-minting step. The bucket itself is private.
    return presign_uploads(result, storage.presigned_get_url)


@router.put("/{report_id}")
@inject
async def update_report(
    report_id: UuidPath,
    body: UpdateReportRequest,
    *,
    svc: FromDishka[ReportService],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    report = await svc.update(
        user.id, report_id, body.title, body.abstract, body.visibility,
        language=body.language, nuts_region=body.nuts_region,
    )
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


# Sections are gone. An article is title + abstract + body, and its
# structure is the headings inside the body — there is no second place
# for structure to live. The CRUD, lock and per-section version routes
# that assumed many sections went with them: nothing in the product
# called them (every article in production had exactly one section), and
# section locking was a live-collaboration answer to a problem the
# draft-and-merge model solves differently.


# ── v2 Document API ──────────────────────────────────────────

@router.get("/{report_id}/translations", openapi_extra={"security": []})
@inject
async def list_translations(
    report_id: UuidPath,
    *,
    svc: FromDishka[ReportService],
    user: Annotated[User | None, Depends(get_optional_user)],
) -> dict:
    """Translation metadata (no bodies). Readable by whoever can read the story."""
    uid = user.id if user is not None else None
    report, translations = await svc.list_translations(uid, report_id)
    return {
        "language": report.language,
        "content_version": report.content_version,
        "translations": translations,
    }


@router.get("/{report_id}/translations/{lang}", openapi_extra={"security": []})
@inject
async def get_translation(
    report_id: UuidPath,
    lang: LangPath,
    *,
    svc: FromDishka[ReportService],
    user: Annotated[User | None, Depends(get_optional_user)],
) -> dict:
    uid = user.id if user is not None else None
    t, outdated = await svc.get_translation(uid, report_id, lang)
    return {
        "lang": t.lang,
        "title": t.title,
        "abstract": t.abstract,
        "content_doc": t.content_json,
        "source_version": t.source_version,
        "outdated": outdated,
        "updated_at": t.updated_at,
    }


@router.put("/{report_id}/translations/{lang}")
@inject
async def save_translation(
    report_id: UuidPath,
    lang: LangPath,
    body: SaveTranslationRequest,
    *,
    svc: FromDishka[ReportService],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Upsert a translation. Saving pins it to the original's current
    content_version, clearing any outdated flag."""
    t = await svc.upsert_translation(
        user.id, report_id, lang, body.title, body.abstract,
        {"tiptap": body.tiptap, "version": body.version},
    )
    return {"ok": True, "lang": t.lang, "source_version": t.source_version}


@router.post("/{report_id}/translations/{lang}/resolve")
@inject
async def resolve_translation(
    report_id: UuidPath,
    lang: LangPath,
    *,
    svc: FromDishka[ReportService],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Mark a translation as reviewed against the current original
    (clears the potentially-outdated flag without editing the text)."""
    await svc.resolve_translation(user.id, report_id, lang)
    return {"ok": True}


@router.delete("/{report_id}/translations/{lang}", status_code=204)
@inject
async def delete_translation(
    report_id: UuidPath,
    lang: LangPath,
    *,
    svc: FromDishka[ReportService],
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    await svc.delete_translation(user.id, report_id, lang)


@router.put("/{report_id}/content")
@inject
async def save_document(
    report_id: UuidPath,
    body: SaveDocumentRequest,
    *,
    svc: FromDishka[ReportService],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Save the full report document as TipTap JSON (v2 format).

    409 when ``base_revision`` is not the current head — the body then
    carries ``current_revision`` and ``current_doc`` so the editor can
    show what it would have overwritten.
    """
    revision = await svc.save_document(
        user.id, report_id,
        {"tiptap": body.tiptap, "version": body.version},
        body.base_revision,
    )
    return {"ok": True, "revision": revision.id}


# ── Revision history ─────────────────────────────────────────


@router.get("/{report_id}/revisions")
@inject
async def list_revisions(
    report_id: UuidPath,
    *,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    svc: FromDishka[ReportService],
    user: Annotated[User, Depends(get_current_user)],
) -> list[dict]:
    """The article's revisions, newest first, each with what it changed."""
    return await svc.list_document_revisions(user.id, report_id, limit)


@router.get(
    "/{report_id}/diff",
    responses={404: {"description": "No such revision."}},
)
@inject
async def diff_revisions(
    report_id: UuidPath,
    *,
    from_revision: Annotated[str | None, Query(alias="from")] = None,
    to_revision: Annotated[str | None, Query(alias="to")] = None,
    svc: FromDishka[ReportService],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Block-level changes between two revisions.

    With no parameters: what the most recent save changed.
    """
    return await svc.diff_document(user.id, report_id,
                                   from_revision, to_revision)


@router.post(
    "/{report_id}/revisions/{revision_id}/restore",
    responses={404: {"description": "No such revision."}},
)
@inject
async def restore_revision(
    report_id: UuidPath,
    revision_id: UuidPath,
    *,
    svc: FromDishka[ReportService],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Restore an older revision as a new one on top of the history."""
    revision = await svc.restore_document_revision(
        user.id, report_id, revision_id)
    return {"ok": True, "revision": revision.id}


# ── Merge requests ───────────────────────────────────────────
#
# Editors only, by design: an unreviewed proposal is not yet a claim the
# platform is making, so readers see the published text and nothing else.


class OpenMergeRequest(BaseModel):
    """Propose the caller's draft as the article's published text."""
    title: str = Field(default="", max_length=200)
    body: str = Field(default="", max_length=4000)


@router.post("/{report_id}/merge-requests", status_code=201)
@inject
async def open_merge_request(
    report_id: UuidPath,
    body: OpenMergeRequest,
    *,
    svc: FromDishka[ReportService],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    mr = await svc.open_merge_request(user.id, report_id, body.title, body.body)
    return await svc.get_merge_request(user.id, report_id, mr.id)


@router.get("/{report_id}/merge-requests")
@inject
async def list_merge_requests(
    report_id: UuidPath,
    *,
    state: Annotated[str | None, Query()] = "open",
    svc: FromDishka[ReportService],
    user: Annotated[User, Depends(get_current_user)],
) -> list[dict]:
    return await svc.list_merge_requests(user.id, report_id, state)


@router.get(
    "/{report_id}/merge-requests/{mr_id}",
    responses={404: {"description": "No such merge request."}},
)
@inject
async def get_merge_request(
    report_id: UuidPath,
    mr_id: UuidPath,
    *,
    svc: FromDishka[ReportService],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """The proposal and the changes it would make to the article."""
    return await svc.get_merge_request(user.id, report_id, mr_id)


@router.post(
    "/{report_id}/merge-requests/{mr_id}/merge",
    responses={
        404: {"description": "No such merge request."},
        409: {"description": "The published text moved on since this "
                             "was proposed."},
    },
)
@inject
async def merge_merge_request(
    report_id: UuidPath,
    mr_id: UuidPath,
    *,
    svc: FromDishka[ReportService],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Publish the proposal. Fast-forward only."""
    return await svc.merge_merge_request(user.id, report_id, mr_id)


@router.post(
    "/{report_id}/merge-requests/{mr_id}/close",
    responses={404: {"description": "No such merge request."}},
)
@inject
async def close_merge_request(
    report_id: UuidPath,
    mr_id: UuidPath,
    *,
    svc: FromDishka[ReportService],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Withdraw it. The draft and its revisions stay exactly where they are."""
    return await svc.close_merge_request(user.id, report_id, mr_id)


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
    # Upload goes through the same authorisation seam every other
    # mutating story op uses — the service-level gate audits the
    # attempt and enforces editor-or-grant.
    await svc.require_upload(user.id, report_id)

    raw = await file.read()
    # InvalidInput from the pipeline propagates to the app-level
    # handler → 400. No try/except here.
    cleaned = scan_and_sanitise(raw, clamd_client=_get_clamd())

    storage = _get_storage()
    key = storage.upload(report_id, cleaned.data, cleaned.content_type)
    return {"url": storage.get_url(key), "key": key}


@router.get("/{report_id}/effective-access")
@inject
async def report_effective_access(
    report_id: UuidPath,
    *,
    svc: FromDishka[ReportService],
    user: Annotated[User, Depends(get_current_user)],
) -> list[dict]:
    """Who has access to the article and why (owner / inherited / direct)."""
    return await svc.effective_access(user.id, report_id)
