"""Report (story) service — every story-mutation policy decision
routes through :class:`AuthorizationService`.

Reports are the central resource on the platform, so the policy check
is also the most layered: visibility (public_open / public_auth /
private), ownership, *and* an explicit grant table (viewer /
commenter / editor / owner) maintained by :class:`PermissionService`.

Each method pre-loads the report (so a missing id returns 404, not
403), pre-resolves the caller's effective grant from
:class:`PermissionService`, and bundles both into a ``ResourceRef``
that the policy can decide on without touching the database.
"""
from __future__ import annotations

import re
from datetime import datetime, time, timezone

from src.domain.report import Report, ReportTranslation, Section
from src.repositories.report_repository import ReportRepository
from src.services.activity_service import ActivityService
from src.services.authz import (
    Action,
    AuthorizationService,
    ResourceRef,
)
from src.services.authz.policy import Principal
from src.services.exceptions import Conflict, InvalidInput, NotFound
from src.services.access_inheritance import AccessInheritance, max_level
from src.repositories.group_repository import GroupRepository
from src.repositories.user_repository import UserRepository
from src.services.permission_service import LEVEL_HIERARCHY
from src.services.permission_service import PermissionService
from src.services.sanitize import sanitize_html, sanitize_text

_LANG_RE = re.compile(r"[a-z]{2}")

DEFAULT_LOCK_TTL = 300  # 5 minutes



def _day_start(iso: str | None) -> datetime | None:
    """Parse an ISO yyyy-mm-dd into a tz-aware start-of-day datetime."""
    if not iso:
        return None
    d = datetime.strptime(iso, "%Y-%m-%d").date()
    return datetime.combine(d, time.min, tzinfo=timezone.utc)


def _day_end(iso: str | None) -> datetime | None:
    """Parse an ISO yyyy-mm-dd into a tz-aware end-of-day datetime
    (inclusive upper bound)."""
    if not iso:
        return None
    d = datetime.strptime(iso, "%Y-%m-%d").date()
    return datetime.combine(d, time.max, tzinfo=timezone.utc)


class ReportService:  # pylint: disable=too-many-public-methods
    # One service per aggregate: report + sections + versions + locks +
    # tags + translations share authz + activity plumbing here.
    def __init__(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        reports: ReportRepository,
        perms: PermissionService,
        authz: AuthorizationService,
        inheritance: AccessInheritance,
        users: UserRepository,
        groups: GroupRepository,
        activity: ActivityService,
    ) -> None:
        self._reports = reports
        self._activity = activity
        self._perms = perms
        self._authz = authz
        self._inheritance = inheritance
        self._users = users
        self._groups = groups

    async def _load_for(
        self, user_id: str | None, report_id: str, action: Action,
    ) -> tuple[Report, Principal | None]:
        """Load the report and run the policy check.

        Returns ``(report, principal)`` so the caller can keep using
        the report without a second DB hit. Pre-loads the principal +
        effective grant so the policy stays pure.

        Raises :class:`NotFound` for a missing report; the legacy
        "leak existence via 403 vs 404" mitigation is now superseded
        by the visibility check inside the policy — anon callers can
        only reach this with a `public_open` story (other paths use
        :meth:`get_viewable`) so non-existent vs private is the same
        from the outside.
        """
        report = await self._reports.get_by_id(report_id)
        if report is None:
            raise NotFound(f"Report {report_id} not found")
        principal = await self._authz.principal(user_id)
        grant = await self._perms.effective_grant(user_id, report_id) if user_id else None
        if user_id:
            # An investigation the article belongs to confers access by role.
            grant = max_level(grant, await self._inheritance.inherited_report_level(user_id, report))
        await self._authz.require(
            principal, action,
            ResourceRef.for_story(report, effective_grant=grant),
        )
        return report, principal

    async def create(
        self, user_id: str, title: str,
        abstract: str | None = None,
        parent_id: str | None = None,
    ) -> Report:
        principal = await self._authz.principal(user_id)
        await self._authz.require(principal, Action.STORIES_CREATE)
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
        await self._activity.record(user_id, "story", report.id or "", "created", report.title)
        return report

    async def get(self, user_id: str, report_id: str) -> Report:
        report, _ = await self._load_for(user_id, report_id, Action.STORIES_READ)
        return report

    async def get_viewable(
        self, user_id: str | None, report_id: str,
    ) -> Report:
        """Fetch a report, honouring its visibility against an optional user.

        Anonymous callers (``user_id=None``) only see reports with
        visibility ``public_open``. Authenticated callers go through
        the standard policy check (which also honours ``public_auth``).

        Anonymous attempts to access non-public reports return 404 —
        don't leak whether a private report exists by giving a
        distinguishable 403 vs 404.
        """
        report = await self._reports.get_by_id(report_id)
        if report is None:
            raise NotFound(f"Report {report_id} not found")
        if user_id is None:
            # Anonymous: short-circuit to 404 for any non-open story
            # so we don't leak existence via a distinguishable 403.
            # public_open is the one path open to anon — no policy
            # decision needed (the AuthorizationService denies None
            # principals by design; this is the documented exception).
            if report.visibility != "public_open":
                raise NotFound(f"Report {report_id} not found")
            return report
        principal = await self._authz.principal(user_id)
        grant = await self._perms.effective_grant(user_id, report_id)
        grant = max_level(grant, await self._inheritance.inherited_report_level(user_id, report))
        await self._authz.require(
            principal, Action.STORIES_READ,
            ResourceRef.for_story(report, effective_grant=grant),
        )
        return report

    # pylint: disable-next=too-many-arguments,too-many-positional-arguments
    async def update(
        self,
        user_id: str,
        report_id: str,
        title: str | None = None,
        abstract: str | None = None,
        visibility: str | None = None,
        language: str | None = None,
    ) -> Report:
        report, _ = await self._load_for(user_id, report_id, Action.STORIES_EDIT_META)
        translatable_changed = False
        if title is not None:
            clean = sanitize_text(title)
            translatable_changed = translatable_changed or clean != report.title
            report.title = clean
        if abstract is not None:
            clean = sanitize_text(abstract)
            translatable_changed = translatable_changed or clean != report.abstract
            report.abstract = clean
        if visibility is not None:
            report.visibility = visibility
        if language is not None:
            report.language = language
        # Title/abstract are part of what translators translate — a real
        # change makes existing translations potentially outdated.
        if translatable_changed:
            report.content_version += 1
        report = await self._reports.update(report)
        await self._activity.record(user_id, "story", report_id, "updated", report.title)
        return report

    async def delete(self, user_id: str, report_id: str) -> None:
        report, _ = await self._load_for(user_id, report_id, Action.STORIES_DELETE)
        await self._reports.delete(report_id)
        await self._activity.record(user_id, "story", report_id, "deleted", report.title)

    async def add_section(self, user_id: str, report_id: str, content: dict) -> Section:
        await self._load_for(user_id, report_id, Action.STORIES_EDIT)
        section = Section(content_json=_sanitize_section(content))
        return await self._reports.add_section(report_id, section)

    async def edit_section(self, user_id: str, section_id: str, content: dict) -> Section:
        section = await self._reports.get_section(section_id)
        if section is None:
            raise NotFound(f"Section {section_id} not found")
        await self._load_for(user_id, section.report_id, Action.STORIES_EDIT)
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
        await self._load_for(user_id, section.report_id, Action.STORIES_EDIT)
        await self._reports.delete_section(section_id)

    async def acquire_lock(self, user_id: str, section_id: str) -> bool:
        section = await self._reports.get_section(section_id)
        if section is None:
            raise NotFound(f"Section {section_id} not found")
        await self._load_for(user_id, section.report_id, Action.STORIES_LOCK_SECTION)
        return await self._reports.acquire_lock(section_id, user_id, DEFAULT_LOCK_TTL)

    async def release_lock(self, user_id: str, section_id: str) -> None:
        # No authz check: only the holder can release, enforced at the
        # repo level by the WHERE-clause on lock_holder.
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

    # pylint: disable-next=too-many-arguments,too-many-positional-arguments
    async def search_public(
        self, query: str, limit: int, offset: int,
        authenticated: bool = False,
        date_from: str | None = None, date_to: str | None = None,
    ) -> list[Report]:
        """Keyword search over public stories (title + abstract),
        visibility-aware like list_public. ``date_from``/``date_to`` are
        inclusive ISO ``yyyy-mm-dd`` strings filtered on ``created_at``."""
        q = (query or "").strip()
        if not q:
            return []
        return await self._reports.search_public(
            q, limit, offset, authenticated=authenticated,
            date_from=_day_start(date_from), date_to=_day_end(date_to),
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
        report, _ = await self._load_for(user_id, report_id, Action.STORIES_EDIT)
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
        # Every document save is a content change from a translator's
        # point of view — existing translations become maybe-outdated.
        report.content_version += 1
        await self._reports.update(report)

    # ── translations ───────────────────────────────────────────
    # An article has one original text (report.title/abstract/document,
    # in report.language) and any number of translations keyed by lang.
    # Each translation pins the content_version it was made against;
    # a lower pin than the report's current version marks it as
    # potentially outdated until a translator updates or resolves it.

    @staticmethod
    def _validate_lang(lang: str) -> str:
        if not _LANG_RE.fullmatch(lang or ""):
            raise InvalidInput("lang must be a two-letter ISO 639-1 code")
        return lang

    async def translation_overlay(
        self, reports: list[Report], lang: str | None
    ) -> dict[str, dict]:
        """Feed-card overlay: for stories translated into ``lang``, the
        translated title/abstract (+ outdated flag) keyed by story id.
        Stories whose original already is ``lang`` are left alone."""
        if not lang or not _LANG_RE.fullmatch(lang):
            return {}
        ids = [r.id for r in reports if r.id and r.language != lang]
        if not ids:
            return {}
        by_id = {r.id: r for r in reports}
        out: dict[str, dict] = {}
        for t in await self._reports.get_translation_summaries(ids, lang):
            report = by_id.get(t.report_id)
            if report is None:
                continue
            out[t.report_id] = {
                "title": t.title,
                "abstract": t.abstract,
                "outdated": t.source_version < report.content_version,
            }
        return out

    async def list_translations(
        self, user_id: str | None, report_id: str
    ) -> tuple[Report, list[dict]]:
        """Translation metadata for the story page + editor: no bodies."""
        report = await self.get_viewable(user_id, report_id)
        rows = await self._reports.list_translations(report_id)
        return report, [
            {
                "lang": t.lang,
                "title": t.title,
                "outdated": t.source_version < report.content_version,
                "updated_at": t.updated_at,
            }
            for t in rows
        ]

    async def get_translation(
        self, user_id: str | None, report_id: str, lang: str
    ) -> tuple[ReportTranslation, bool]:
        """One full translation + its outdated flag. Read follows the story."""
        report = await self.get_viewable(user_id, report_id)
        t = await self._reports.get_translation(report_id, self._validate_lang(lang))
        if t is None:
            raise NotFound(f"No {lang} translation for story {report_id}")
        return t, t.source_version < report.content_version

    async def upsert_translation(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self, user_id: str, report_id: str, lang: str,
        title: str, abstract: str | None, content: dict,
    ) -> ReportTranslation:
        """Create or replace a translation; it becomes current-by-definition
        (pinned to the report's content_version at save time)."""
        report, _ = await self._load_for(user_id, report_id, Action.STORIES_EDIT)
        self._validate_lang(lang)
        translation = ReportTranslation(
            report_id=report_id,
            lang=lang,
            title=sanitize_text(title),
            abstract=sanitize_text(abstract) if abstract is not None else None,
            content_json=content,
            source_version=report.content_version,
            created_by=user_id,
        )
        saved = await self._reports.upsert_translation(translation)
        await self._activity.record(
            user_id, "story", report_id, "translated", f"{report.title} [{lang}]")
        return saved

    async def resolve_translation(self, user_id: str, report_id: str, lang: str) -> None:
        """Mark a translation as up to date with the current original —
        the translator reviewed the original's changes and decided the
        existing translation still stands."""
        report, _ = await self._load_for(user_id, report_id, Action.STORIES_EDIT)
        t = await self._reports.get_translation(report_id, self._validate_lang(lang))
        if t is None:
            raise NotFound(f"No {lang} translation for story {report_id}")
        t.source_version = report.content_version
        await self._reports.upsert_translation(t)

    async def delete_translation(self, user_id: str, report_id: str, lang: str) -> None:
        await self._load_for(user_id, report_id, Action.STORIES_EDIT)
        await self._reports.delete_translation(report_id, self._validate_lang(lang))

    async def require_upload(self, user_id: str, report_id: str) -> None:
        """Authorisation gate for image/SVG upload.

        Exposed as a method so the upload handler in
        :mod:`src.api.routers.reports` can go through the same
        single-point check as everything else, rather than reaching
        into PermissionService directly.
        """
        await self._load_for(user_id, report_id, Action.STORIES_UPLOAD)


    async def effective_access(self, user_id: str, report_id: str) -> list[dict]:
        """Who has access to the article and why: each principal's highest level
        + source (owner / inherited:<role> / direct). READ-gated."""
        report, _ = await self._load_for(user_id, report_id, Action.STORIES_READ)
        rows: dict[str, dict] = {}
        if report.created_by:
            _add_access(rows, "user", report.created_by, "owner", "owner")
        for uid, role, level in await self._inheritance.inherited_members_for_report(report):
            _add_access(rows, "user", uid, level, f"inherited:{role}")
        for g in await self._perms.list_collaborators(report_id):
            if g.user_id:
                _add_access(rows, "user", g.user_id, g.level, "direct")
            elif g.group_id:
                _add_access(rows, "group", g.group_id, g.level, "direct")
        return [await self._enrich_access(info) for info in rows.values()]

    async def _enrich_access(self, info: dict) -> dict:
        entry = {"level": info["level"], "source": info["source"]}
        if info["kind"] == "user":
            u = await self._users.get_by_id(info["id"])
            entry.update({
                "user_id": info["id"],
                "email": u.email if u else None, "name": u.name if u else None,
            })
        else:
            grp = await self._groups.get_by_id(info["id"])
            entry.update({"group_id": info["id"], "name": grp.name if grp else None})
        return entry


def _add_access(rows: dict, kind: str, pid: str, level: str, source: str) -> None:
    """Record a principal's grant, keeping only the highest level seen."""
    key = f"{kind}:{pid}"
    cur = rows.get(key)
    if cur is None or LEVEL_HIERARCHY.get(level, 0) > LEVEL_HIERARCHY.get(cur["level"], 0):
        rows[key] = {"kind": kind, "id": pid, "level": level, "source": source}


def _sanitize_section(content: dict) -> dict:
    """Sanitize the HTML inside a section content dict."""
    if "html" in content:
        content = {**content, "html": sanitize_html(content["html"])}
    return content
