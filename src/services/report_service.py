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

import hashlib
import json

import re
from datetime import datetime, time, timezone

from src.domain.report import (
    DocRevision,
    MergeRequest,
    Report,
    ReportTranslation,
    Section,
)
from src.services import doc_diff
from src.repositories.report_repository import ReportRepository
from src.services.activity_service import ActivityService
from src.services.authz import (
    Action,
    AuthorizationService,
    ResourceRef,
)
from src.services.authz.policy import Principal
from src.services.nuts import normalize_nuts
from src.services.exceptions import Conflict, InvalidInput, NotFound
from src.services.access_inheritance import AccessInheritance, max_level
from src.repositories.group_repository import GroupRepository
from src.repositories.user_repository import UserRepository
from src.services.permission_service import LEVEL_HIERARCHY
from src.services.permission_service import PermissionService
from src.services.sanitize import sanitize_html, sanitize_text

_LANG_RE = re.compile(r"[a-z]{2}")




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


#: One wording for "that revision is not part of this article", whether
#: the id is unknown or belongs to somebody else's story. The distinction
#: is deliberately invisible: saying "exists, but not here" is what an
#: enumeration attack needs.
_NO_SUCH_REVISION = "No such revision."

#: Same reasoning as above for proposals.
_NO_SUCH_MERGE_REQUEST = "No such merge request."


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
        nuts_region: str | None = None,
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
        if nuts_region is not None:
            # Region tag isn't translated content, so it never bumps the
            # translation content_version.
            report.nuts_region = normalize_nuts(nuts_region, report.nuts_region)
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

    # add_section / edit_section / delete_section / acquire_lock /
    # release_lock lived here. An article is one document now: it is
    # written through save_document and read through get_sections, and
    # its structure is the headings in the body. Section locking was the
    # live-collaboration answer to concurrent editing; the draft-branch
    # model answers it with a baseline check instead.

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

    @staticmethod
    def _hash(content: dict) -> str:
        """Content address for a document.

        Canonical JSON — sorted keys, no incidental whitespace — so that
        two saves of the same document hash the same however the client
        happened to serialise it.
        """
        canonical = json.dumps(content, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    async def document_head(self, report_id: str) -> DocRevision | None:
        """The revision a reader of this article is looking at."""
        branch = await self._reports.get_branch(report_id, None)
        if branch is None:
            return None
        return await self._reports.get_revision(branch.head_revision_id)

    async def draft_head(self, user_id: str, report_id: str) -> DocRevision | None:
        """Where this editor's own draft currently points, if they have one."""
        branch = await self._reports.get_branch(report_id, user_id)
        if branch is None:
            return None
        return await self._reports.get_revision(branch.head_revision_id)

    # Six, and each one is load-bearing: who is writing, which article,
    # what, on top of what, and whether a human or the assistant wrote it.
    # pylint: disable-next=too-many-arguments,too-many-positional-arguments
    async def _require_baseline(
        self, report_id: str, user_id: str, base_revision: str | None,
    ) -> tuple[str | None, DocRevision | None]:
        """The revision this editor is writing on top of.

        Refuses a save that names a different one: that save was written
        against text which is no longer there, and applying it would
        discard whatever replaced it — the silent overwrite this whole
        mechanism exists to prevent.
        """
        draft = await self._reports.get_branch(report_id, user_id)
        main_head = await self.document_head(report_id)
        # A new draft starts where the published text is; an article with
        # neither has nothing to be written on top of yet.
        if draft is not None:
            head_id = draft.head_revision_id
        elif main_head is not None:
            head_id = main_head.id
        else:
            head_id = None

        if base_revision != head_id:
            current = (await self._reports.get_revision(head_id)
                       if head_id else None)
            raise Conflict(
                "the document changed since you loaded it",
                payload={
                    "current_revision": head_id,
                    "current_doc": current.content_json if current else None,
                },
            )
        return head_id, main_head

    async def _publish_first_save(
        self, report_id: str, revision: DocRevision,
    ) -> None:
        """An article's first save is also its first published version.

        There is nothing to review it against, and a story nobody can
        read until it merges is a worse default than one whose first
        draft is its first version.
        """
        await self._reports.set_branch_head(report_id, None, revision.id)
        await self._write_read_model(report_id, revision.content_json)
        report = await self._reports.get_by_id(report_id)
        if report is not None:
            report.content_version += 1
            await self._reports.update(report)

    # Six, and each one is load-bearing: who is writing, which article,
    # what, on top of what, and whether a human or the assistant wrote it.
    # pylint: disable-next=too-many-arguments,too-many-positional-arguments
    async def save_document(
        self, user_id: str, report_id: str, content: dict,
        base_revision: str | None = None, *, author_kind: str = "human",
    ) -> DocRevision:
        """Commit a new revision on this editor's draft branch.

        Writing goes to the draft, never straight to main: the published
        text changes when a merge request merges, not when somebody's
        autosave timer fires. The draft is created off main's head the
        first time an editor saves.
        """
        await self._load_for(user_id, report_id, Action.STORIES_EDIT)
        head_id, main_head = await self._require_baseline(
            report_id, user_id, base_revision)

        content_hash = self._hash(content)
        head = (await self._reports.get_revision(head_id)) if head_id else None
        if head is not None and head.content_hash == content_hash:
            # Nothing changed. An autosave that re-sends the same document
            # should not manufacture a revision for the history to carry.
            return head

        revision = await self._reports.add_revision(DocRevision(
            report_id=report_id, parent_id=head_id, content_json=content,
            content_hash=content_hash, author_id=user_id,
            author_kind=author_kind,
        ))
        draft = await self._reports.get_branch(report_id, user_id)
        base_id = draft.base_revision_id if draft else None
        if base_id is None:
            base_id = main_head.id if main_head else revision.id
        await self._reports.set_branch_head(
            report_id, user_id, revision.id, base_revision_id=base_id)

        if main_head is None:
            await self._publish_first_save(report_id, revision)
        return revision

    async def _write_read_model(self, report_id: str, content: dict) -> None:
        """Keep the section row in step with main's head.

        Readers still come through it; phase 5 moves them onto the
        revision directly.
        """
        sections = await self._reports.get_sections(report_id)
        if sections:
            sections[0].content_json = content
            await self._reports.update_section(sections[0])
            for s in sections[1:]:
                await self._reports.delete_section(s.id)
        else:
            await self._reports.add_section(
                report_id, Section(content_json=content))

    # ── revision history ───────────────────────────────────────

    async def list_document_revisions(
        self, user_id: str, report_id: str, limit: int = 50,
    ) -> list[dict]:
        """The article's history, newest first, with what each save did.

        Each row carries the difference from its parent, so a reader can
        see "three paragraphs and a widget" without fetching two full
        documents per row.
        """
        await self._load_for(user_id, report_id, Action.STORIES_READ)
        revisions = await self._reports.list_revisions(report_id, limit)
        by_id = {r.id: r for r in revisions}

        rows = []
        for revision in revisions:
            parent = by_id.get(revision.parent_id)
            if parent is None and revision.parent_id:
                parent = await self._reports.get_revision(revision.parent_id)
            rows.append({
                "id": revision.id,
                "parent_id": revision.parent_id,
                "author_id": revision.author_id,
                "author_kind": revision.author_kind,
                "created_at": (revision.created_at.isoformat()
                               if revision.created_at else None),
                "changes": doc_diff.summary(doc_diff.diff(
                    parent.content_json if parent else None,
                    revision.content_json)),
            })
        return rows

    async def diff_document(
        self, user_id: str, report_id: str,
        from_revision: str | None, to_revision: str | None,
    ) -> dict:
        """Block operations between two revisions of one article.

        Defaults are the useful ones: ``to`` is the current head and
        ``from`` is its parent, so "what changed last" costs no
        parameters.
        """
        await self._load_for(user_id, report_id, Action.STORIES_READ)

        if to_revision:
            target = await self._reports.get_revision(to_revision)
        else:
            # "What changed last" means the caller's own draft when they
            # have one: that is the work they are looking at.
            target = (await self.draft_head(user_id, report_id)
                      or await self.document_head(report_id))
        if target is None or target.report_id != report_id:
            raise NotFound(_NO_SUCH_REVISION)

        if from_revision:
            base = await self._reports.get_revision(from_revision)
            if base is None or base.report_id != report_id:
                raise NotFound(_NO_SUCH_REVISION)
        elif target.parent_id:
            base = await self._reports.get_revision(target.parent_id)
        else:
            # The first revision: everything in it is an addition.
            base = None

        operations = doc_diff.diff(
            base.content_json if base else None, target.content_json)
        return {
            "from": base.id if base else None,
            "to": target.id,
            "changes": doc_diff.summary(operations),
            "operations": operations,
        }

    async def restore_document_revision(
        self, user_id: str, report_id: str, revision_id: str,
    ) -> DocRevision:
        """Bring an older revision back as a NEW revision on top.

        Never a rewrite of history: restoring is an edit like any other,
        and the revision it restored from stays exactly where it was. A
        history you can quietly rewrite is not evidence of anything.
        """
        await self._load_for(user_id, report_id, Action.STORIES_EDIT)
        wanted = await self._reports.get_revision(revision_id)
        if wanted is None or wanted.report_id != report_id:
            raise NotFound(_NO_SUCH_REVISION)
        # Restoring is an edit, so it lands where edits land: on the
        # caller's draft, on top of whatever is already there.
        head = (await self.draft_head(user_id, report_id)
                or await self.document_head(report_id))
        return await self.save_document(
            user_id, report_id, wanted.content_json,
            head.id if head else None)

    # ── merge requests ─────────────────────────────────────────

    async def _behind_by(self, report_id: str, base_revision: str) -> int:
        """How many revisions main has taken since ``base_revision``."""
        head = await self.document_head(report_id)
        steps = 0
        cursor = head
        while cursor is not None and cursor.id != base_revision and steps < 500:
            steps += 1
            cursor = (await self._reports.get_revision(cursor.parent_id)
                      if cursor.parent_id else None)
        return steps if cursor is not None else -1

    async def open_merge_request(
        self, user_id: str, report_id: str, title: str = "", body: str = "",
    ) -> MergeRequest:
        """Propose this editor's draft as the article's published text."""
        await self._load_for(user_id, report_id, Action.STORIES_EDIT)
        draft = await self._reports.get_branch(report_id, user_id)
        if draft is None:
            raise InvalidInput("You have no draft to propose.")

        main_head = await self.document_head(report_id)
        if main_head is not None and draft.head_revision_id == main_head.id:
            raise InvalidInput("Your draft matches the published text.")

        existing = [m for m in await self._reports.list_merge_requests(
            report_id, "open") if m.author_id == user_id]
        if existing:
            # The draft branch is singular, so a second open request for it
            # would be the same proposal twice. Move the existing one.
            mr = existing[0]
            mr.source_head = draft.head_revision_id
            mr.title = title or mr.title
            mr.body = body or mr.body
            return await self._reports.update_merge_request(mr)

        return await self._reports.add_merge_request(MergeRequest(
            report_id=report_id, author_id=user_id,
            title=title or "Proposed revision", body=body,
            source_head=draft.head_revision_id,
            target_base=main_head.id if main_head else draft.head_revision_id,
        ))

    async def list_merge_requests(
        self, user_id: str, report_id: str, state: str | None = "open",
    ) -> list[dict]:
        """Open proposals for this article — for people who may edit it.

        Readers see the published text and nothing else: a proposal
        nobody has reviewed is not yet a claim the platform is making.
        """
        await self._load_for(user_id, report_id, Action.STORIES_EDIT)
        rows = await self._reports.list_merge_requests(report_id, state)
        return [await self._mr_view(m) for m in rows]

    async def get_merge_request(
        self, user_id: str, report_id: str, mr_id: str,
    ) -> dict:
        """One proposal, with the changes it would make to the article."""
        await self._load_for(user_id, report_id, Action.STORIES_EDIT)
        mr = await self._reports.get_merge_request(mr_id)
        if mr is None or mr.report_id != report_id:
            raise NotFound(_NO_SUCH_MERGE_REQUEST)

        base = await self._reports.get_revision(mr.target_base)
        source = await self._reports.get_revision(mr.source_head)
        operations = doc_diff.diff(
            base.content_json if base else None,
            source.content_json if source else None)
        view = await self._mr_view(mr)
        view["operations"] = operations
        return view

    async def _mr_view(self, mr: MergeRequest) -> dict:
        """The row a reviewer reads, including whether it can merge."""
        base = await self._reports.get_revision(mr.target_base)
        source = await self._reports.get_revision(mr.source_head)
        changes = doc_diff.summary(doc_diff.diff(
            base.content_json if base else None,
            source.content_json if source else None))
        behind = 0
        if mr.state == "open":
            behind = await self._behind_by(mr.report_id, mr.target_base)
        return {
            "id": mr.id,
            "report_id": mr.report_id,
            "author_id": mr.author_id,
            "title": mr.title,
            "body": mr.body,
            "state": mr.state,
            "source_head": mr.source_head,
            "target_base": mr.target_base,
            "created_at": mr.created_at.isoformat() if mr.created_at else None,
            "merged_at": mr.merged_at.isoformat() if mr.merged_at else None,
            "merged_by": mr.merged_by,
            "self_merged": mr.self_merged,
            "changes": changes,
            # Nothing expires and nothing is auto-rebased: a draft that
            # has fallen behind is shown as such, and its author decides.
            "behind": behind,
            "can_fast_forward": mr.state == "open" and behind == 0,
        }

    async def merge_merge_request(
        self, user_id: str, report_id: str, mr_id: str,
    ) -> dict:
        """Publish a proposal by moving main to its head.

        Only a fast-forward: if main has moved since the proposal was
        opened, merging would discard whatever moved it. That case is
        refused with the difference attached rather than resolved by
        guesswork.

        An author may merge their own proposal — solo authorship is the
        normal case here — and the merge records that nobody else read it.
        """
        await self._load_for(user_id, report_id, Action.STORIES_EDIT)
        mr = await self._reports.get_merge_request(mr_id)
        if mr is None or mr.report_id != report_id:
            raise NotFound(_NO_SUCH_MERGE_REQUEST)
        if mr.state != "open":
            raise InvalidInput(f"This proposal is already {mr.state}.")

        behind = await self._behind_by(report_id, mr.target_base)
        if behind != 0:
            main_head = await self.document_head(report_id)
            base = await self._reports.get_revision(mr.target_base)
            raise Conflict(
                "the published text moved on since this was proposed",
                payload={
                    "behind": behind,
                    "current_revision": main_head.id if main_head else None,
                    "operations": doc_diff.diff(
                        base.content_json if base else None,
                        main_head.content_json if main_head else None),
                },
            )

        source = await self._reports.get_revision(mr.source_head)
        if source is None:
            raise NotFound(_NO_SUCH_REVISION)

        await self._reports.set_branch_head(report_id, None, source.id)
        await self._write_read_model(report_id, source.content_json)
        report = await self._reports.get_by_id(report_id)
        if report is not None:
            report.content_version += 1
            await self._reports.update(report)

        mr.state = "merged"
        mr.merged_at = datetime.now(timezone.utc)
        mr.merged_by = user_id
        mr.merged_revision_id = source.id
        mr.self_merged = user_id == mr.author_id
        merged = await self._reports.update_merge_request(mr)
        return await self._mr_view(merged)

    async def close_merge_request(
        self, user_id: str, report_id: str, mr_id: str,
    ) -> dict:
        """Withdraw a proposal. The draft and its revisions stay put."""
        await self._load_for(user_id, report_id, Action.STORIES_EDIT)
        mr = await self._reports.get_merge_request(mr_id)
        if mr is None or mr.report_id != report_id:
            raise NotFound(_NO_SUCH_MERGE_REQUEST)
        if mr.state != "open":
            raise InvalidInput(f"This proposal is already {mr.state}.")
        mr.state = "closed"
        return await self._mr_view(
            await self._reports.update_merge_request(mr))

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
