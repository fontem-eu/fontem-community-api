from __future__ import annotations

from datetime import datetime

from abc import ABC, abstractmethod

from src.domain.report import (
    DocBranch,
    DocRevision,
    Review,
    ReviewComment,
    ReviewReviewer,
    Report,
    ReportTranslation,
    Section,
    SectionVersion,
)


class ReportRepository(ABC):  # pylint: disable=too-many-public-methods
    # Interface mirrors the report aggregate (see PgReportRepository).
    @abstractmethod
    async def create(self, report: Report) -> Report: ...

    @abstractmethod
    async def get_by_id(self, report_id: str) -> Report | None: ...

    @abstractmethod
    async def update(self, report: Report) -> Report: ...

    @abstractmethod
    async def delete(self, report_id: str) -> None: ...

    @abstractmethod
    async def list_for_user(self, user_id: str, limit: int, offset: int) -> list[Report]: ...

    @abstractmethod
    async def list_public(
        self, limit: int, offset: int, authenticated: bool = False,
        tag: str | None = None, author_id: str | None = None,
    ) -> list[Report]: ...

    @abstractmethod
    # pylint: disable-next=too-many-arguments,too-many-positional-arguments
    async def search_public(
        self, query: str, limit: int, offset: int,
        authenticated: bool = False,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> list[Report]: ...

    # ── Tags (story side) ─────────────────────────────────────
    # Tags are slugs (`[a-z0-9-]`). The service layer normalises
    # before calling these methods; the repo treats them as opaque
    # strings.

    @abstractmethod
    async def get_story_tags(self, report_id: str) -> list[str]: ...

    @abstractmethod
    async def set_story_tags(self, report_id: str, tags: list[str]) -> None:
        """Replace the full tag set for a story.

        Atomic delete-then-insert; the service layer enforces the
        ≤3 limit before calling.
        """

    @abstractmethod
    async def list_distinct_tags(self) -> list[tuple[str, int]]:
        """Return ``(tag, story_count)`` pairs for every tag in use,
        ordered by descending count then alphabetical. Used by the
        feed's browse-by-tag chip strip; cardinality is small."""

    @abstractmethod
    async def add_section(self, report_id: str, section: Section) -> Section: ...

    @abstractmethod
    async def update_section(self, section: Section) -> Section: ...

    @abstractmethod
    async def delete_section(self, section_id: str) -> None: ...

    @abstractmethod
    async def get_section(self, section_id: str) -> Section | None: ...

    @abstractmethod
    async def get_sections(self, report_id: str) -> list[Section]: ...

    # ── document revisions ─────────────────────────────────────
    #
    # The revision chain and the branch pointers into it. A branch with
    # ``owner_id`` None is main — the published text.

    @abstractmethod
    async def add_revision(self, revision: DocRevision) -> DocRevision: ...

    @abstractmethod
    async def get_revision(self, revision_id: str) -> DocRevision | None: ...

    @abstractmethod
    async def list_revisions(
        self, report_id: str, limit: int,
    ) -> list[DocRevision]: ...

    @abstractmethod
    async def get_branch(
        self, report_id: str, owner_id: str | None,
    ) -> DocBranch | None: ...

    @abstractmethod
    async def set_branch_head(
        self, report_id: str, owner_id: str | None,
        head_revision_id: str, base_revision_id: str | None = None,
        *, expected_head: str | None = None, cas: bool = False,
    ) -> DocBranch | None:
        """Move a branch pointer, optionally only if it has not moved.

        With `cas=True` this is a compare-and-swap: the write applies only
        while the branch head is still `expected_head` (or the branch does
        not exist yet, when that is None), and returns None otherwise. The
        caller turns that None into the 409 it would have raised had it
        seen the newer head in the first place.

        The flag exists because reading the head and then writing it are
        two statements, and two saves that interleave between them both
        pass the read. That is not hypothetical: on 2026-09-02 two saves
        41ms apart wrote sibling revisions off one parent, and the second
        pointer write buried a document containing four charts under one
        containing a single chart.
        """

    # ── reviews ────────────────────────────────────────────────

    @abstractmethod
    async def add_review(self, review: Review) -> Review: ...

    @abstractmethod
    async def get_review(self, review_id: str) -> Review | None: ...

    @abstractmethod
    async def list_reviews(
        self, report_id: str, state: str | None = None,
        kind: str | None = None,
    ) -> list[Review]: ...

    @abstractmethod
    async def update_review(self, review: Review) -> Review: ...

    @abstractmethod
    async def reviews_for_user(self, user_id: str) -> list[Review]:
        """Everything this person authored or was invited to read."""

    @abstractmethod
    async def add_reviewer(self, reviewer: ReviewReviewer) -> ReviewReviewer: ...

    @abstractmethod
    async def list_reviewers(self, review_id: str) -> list[ReviewReviewer]: ...

    @abstractmethod
    async def add_review_comment(self, comment: ReviewComment) -> ReviewComment: ...

    @abstractmethod
    async def list_review_comments(self, review_id: str) -> list[ReviewComment]: ...

    @abstractmethod
    async def get_review_comment(self, comment_id: str) -> ReviewComment | None: ...

    @abstractmethod
    async def update_review_comment(self, comment: ReviewComment) -> ReviewComment: ...

    @abstractmethod
    async def save_version(self, section_id: str, content: dict, user_id: str) -> None: ...

    @abstractmethod
    async def get_versions(self, section_id: str, limit: int) -> list[SectionVersion]: ...

    @abstractmethod
    async def list_children(self, parent_id: str) -> list[Report]: ...

    @abstractmethod
    async def get_translation(self, report_id: str, lang: str) -> ReportTranslation | None: ...

    @abstractmethod
    async def list_translations(self, report_id: str) -> list[ReportTranslation]: ...

    @abstractmethod
    async def upsert_translation(self, translation: ReportTranslation) -> ReportTranslation:
        """Insert or update the (report_id, lang) translation row."""

    @abstractmethod
    async def delete_translation(self, report_id: str, lang: str) -> None: ...

    @abstractmethod
    async def get_translation_summaries(
        self, report_ids: list[str], lang: str
    ) -> list[ReportTranslation]:
        """Translations of one language across many reports (feed overlay)."""


    @abstractmethod
    async def set_dossier(
        self, report_id: str, dossier_id: str | None, parent_id: str | None,
    ) -> None: ...

    @abstractmethod
    async def list_by_dossier(self, dossier_id: str) -> list[Report]: ...

    @abstractmethod
    async def set_investigation(self, report_id: str, investigation_id: str | None) -> None: ...

    @abstractmethod
    async def list_by_investigation(self, investigation_id: str) -> list[Report]: ...
