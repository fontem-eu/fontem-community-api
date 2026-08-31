# ``sqlalchemy.func`` is a magic factory: ``func.count`` is itself a
# generator that returns a SQL expression object, but pylint's static
# introspection sees only the descriptor and lights every ``func.count(…)``
# up as E1102. Suppress at file level — this whole module is the
# SQL-bound repo, every call here goes through the same machinery.
# pylint: disable=not-callable
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

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
from src.infra.postgres.models import (
    DocBranchModel,
    DocRevisionModel,
    ReviewCommentModel,
    ReviewModel,
    ReviewReviewerModel,
    ReportAccessModel,
    ReportModel,
    ReportTranslationModel,
    SectionModel,
    SectionVersionModel,
    StoryTagModel,
)
from src.repositories.report_repository import ReportRepository


class PgReportRepository(ReportRepository):  # pylint: disable=too-many-public-methods
    # The repo mirrors the report aggregate (report + sections + versions
    # + locks + tags + translations); splitting it would split one
    # transaction boundary across classes.
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
            nuts_region=row.nuts_region or "",
            parent_id=row.parent_id,
            dossier_id=row.dossier_id,
            investigation_id=row.investigation_id,
            language=row.language,
            content_version=row.content_version,
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
            nuts_region=report.nuts_region or "",
            parent_id=report.parent_id,
            dossier_id=report.dossier_id,
            investigation_id=report.investigation_id,
            language=report.language,
            content_version=report.content_version,
            created_by=report.created_by,
            created_at=report.created_at or now,
            updated_at=report.updated_at or now,
        )
        self._session.add(model)
        await self._session.commit()
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
        row.nuts_region = report.nuts_region or ""
        row.language = report.language
        row.content_version = report.content_version
        row.updated_at = datetime.now(timezone.utc)
        await self._session.commit()
        return self._report_to_domain(row)

    async def delete(self, report_id: str) -> None:
        await self._session.execute(
            delete(ReportModel).where(ReportModel.id == report_id)
        )
        await self._session.commit()

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

    async def list_public(
        self, limit: int, offset: int, authenticated: bool = False,
        tag: str | None = None, author_id: str | None = None,
    ) -> list[Report]:
        # Anonymous callers only see ``public_open``. Authenticated ones
        # also see ``public_auth`` (visible to any signed-in user but not
        # broadcast publicly).
        allowed = ["public_open"]
        if authenticated:
            allowed.append("public_auth")
        stmt = (
            select(ReportModel)
            .where(ReportModel.visibility.in_(allowed))
        )
        if author_id:
            stmt = stmt.where(ReportModel.created_by == author_id)
        if tag:
            # JOIN against story_tags rather than EXISTS — the planner
            # picks an index-only scan on the (tag, report_id)-shaped
            # composite PK and it's the same cost as EXISTS for our
            # cardinality. Cleaner SQL.
            stmt = stmt.join(
                StoryTagModel, StoryTagModel.report_id == ReportModel.id,
            ).where(StoryTagModel.tag == tag)
        stmt = (
            stmt.order_by(ReportModel.updated_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        return [self._report_to_domain(r) for r in result.scalars().all()]

    # pylint: disable-next=too-many-arguments,too-many-positional-arguments
    async def search_public(
        self, query: str, limit: int, offset: int,
        authenticated: bool = False,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> list[Report]:
        # Same visibility gate as list_public: anon → public_open only,
        # signed-in → also public_auth. Never surfaces private stories.
        allowed = ["public_open"]
        if authenticated:
            allowed.append("public_auth")
        # Escape LIKE wildcards in the user's query so a literal % or _
        # can't widen the match into "everything".
        esc = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{esc}%"
        stmt = (
            select(ReportModel)
            .where(ReportModel.visibility.in_(allowed))
            .where(or_(
                ReportModel.title.ilike(like, escape="\\"),
                ReportModel.abstract.ilike(like, escape="\\"),
            ))
        )
        if date_from is not None:
            stmt = stmt.where(ReportModel.created_at >= date_from)
        if date_to is not None:
            stmt = stmt.where(ReportModel.created_at <= date_to)
        stmt = (
            stmt.order_by(ReportModel.updated_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        return [self._report_to_domain(r) for r in result.scalars().all()]

    # ── Tags ──────────────────────────────────────────────────

    async def get_story_tags(self, report_id: str) -> list[str]:
        stmt = (
            select(StoryTagModel.tag)
            .where(StoryTagModel.report_id == report_id)
            .order_by(StoryTagModel.tag)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def set_story_tags(self, report_id: str, tags: list[str]) -> None:
        # Atomic replace: drop the old set then insert the new. The
        # service layer pre-normalises + dedupes, so we trust the
        # input here.
        await self._session.execute(
            delete(StoryTagModel).where(StoryTagModel.report_id == report_id)
        )
        if tags:
            self._session.add_all([
                StoryTagModel(report_id=report_id, tag=t) for t in tags
            ])
        await self._session.commit()

    async def list_distinct_tags(self) -> list[tuple[str, int]]:
        # One row per tag with story count. `desc()` then alphabetical
        # tiebreaker so the chip strip ordering is stable across loads.
        # Restricted to public stories — private/draft tags shouldn't
        # leak into the public browse.
        stmt = (
            select(StoryTagModel.tag, func.count(StoryTagModel.report_id))
            .join(ReportModel, ReportModel.id == StoryTagModel.report_id)
            .where(ReportModel.visibility.in_(["public_open", "public_auth"]))
            .group_by(StoryTagModel.tag)
            .order_by(func.count(StoryTagModel.report_id).desc(), StoryTagModel.tag)
        )
        result = await self._session.execute(stmt)
        return [(row[0], row[1]) for row in result.all()]

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
        await self._session.commit()
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
        await self._session.commit()
        return self._section_to_domain(row)

    async def delete_section(self, section_id: str) -> None:
        await self._session.execute(
            delete(SectionModel).where(SectionModel.id == section_id)
        )
        await self._session.commit()

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

    # ── document revisions ─────────────────────────────────────

    @staticmethod
    def _revision_to_domain(row: DocRevisionModel) -> DocRevision:
        return DocRevision(
            id=row.id, report_id=row.report_id, parent_id=row.parent_id,
            content_json=row.content_json, content_hash=row.content_hash,
            author_id=row.author_id, author_kind=row.author_kind,
            created_at=row.created_at,
        )

    @staticmethod
    def _branch_to_domain(row: DocBranchModel) -> DocBranch:
        return DocBranch(
            id=row.id, report_id=row.report_id, owner_id=row.owner_id,
            head_revision_id=row.head_revision_id,
            base_revision_id=row.base_revision_id, updated_at=row.updated_at,
        )

    async def add_revision(self, revision: DocRevision) -> DocRevision:
        model = DocRevisionModel(
            report_id=revision.report_id,
            parent_id=revision.parent_id,
            content_json=revision.content_json,
            content_hash=revision.content_hash,
            author_id=revision.author_id,
            author_kind=revision.author_kind,
        )
        self._session.add(model)
        await self._session.commit()
        await self._session.refresh(model)
        return self._revision_to_domain(model)

    async def get_revision(self, revision_id: str) -> DocRevision | None:
        row = (await self._session.execute(
            select(DocRevisionModel).where(DocRevisionModel.id == revision_id)
        )).scalar_one_or_none()
        return self._revision_to_domain(row) if row else None

    async def list_revisions(self, report_id: str, limit: int) -> list[DocRevision]:
        result = await self._session.execute(
            select(DocRevisionModel)
            .where(DocRevisionModel.report_id == report_id)
            .order_by(DocRevisionModel.created_at.desc())
            .limit(limit)
        )
        return [self._revision_to_domain(r) for r in result.scalars().all()]

    async def get_branch(
        self, report_id: str, owner_id: str | None,
    ) -> DocBranch | None:
        stmt = select(DocBranchModel).where(DocBranchModel.report_id == report_id)
        # `== None` rather than `is None`: this is a SQL predicate, and
        # main is the row whose owner IS NULL.
        stmt = stmt.where(DocBranchModel.owner_id.is_(None) if owner_id is None
                          else DocBranchModel.owner_id == owner_id)
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return self._branch_to_domain(row) if row else None

    async def set_branch_head(
        self, report_id: str, owner_id: str | None,
        head_revision_id: str, base_revision_id: str | None = None,
    ) -> DocBranch:
        stmt = select(DocBranchModel).where(DocBranchModel.report_id == report_id)
        stmt = stmt.where(DocBranchModel.owner_id.is_(None) if owner_id is None
                          else DocBranchModel.owner_id == owner_id)
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            row = DocBranchModel(
                report_id=report_id, owner_id=owner_id,
                head_revision_id=head_revision_id,
                base_revision_id=base_revision_id or head_revision_id,
            )
            self._session.add(row)
        else:
            row.head_revision_id = head_revision_id
            if base_revision_id is not None:
                row.base_revision_id = base_revision_id
        await self._session.commit()
        await self._session.refresh(row)
        return self._branch_to_domain(row)

    # ── reviews ────────────────────────────────────────────────

    @staticmethod
    def _review_to_domain(row: ReviewModel) -> Review:
        return Review(
            id=row.id, report_id=row.report_id, kind=row.kind,
            author_id=row.author_id, title=row.title, body=row.body,
            source_head=row.source_head, target_base=row.target_base,
            state=row.state, created_at=row.created_at,
            updated_at=row.updated_at, merged_at=row.merged_at,
            merged_by=row.merged_by,
            merged_revision_id=row.merged_revision_id,
            self_merged=row.self_merged,
        )

    async def add_review(self, review: Review) -> Review:
        model = ReviewModel(
            report_id=review.report_id, kind=review.kind,
            author_id=review.author_id, title=review.title, body=review.body,
            source_head=review.source_head, target_base=review.target_base,
            state=review.state,
        )
        self._session.add(model)
        await self._session.commit()
        await self._session.refresh(model)
        return self._review_to_domain(model)

    async def get_review(self, review_id: str) -> Review | None:
        row = (await self._session.execute(
            select(ReviewModel).where(ReviewModel.id == review_id)
        )).scalar_one_or_none()
        return self._review_to_domain(row) if row else None

    async def list_reviews(
        self, report_id: str, state: str | None = None,
        kind: str | None = None,
    ) -> list[Review]:
        stmt = select(ReviewModel).where(ReviewModel.report_id == report_id)
        if state:
            stmt = stmt.where(ReviewModel.state == state)
        if kind:
            stmt = stmt.where(ReviewModel.kind == kind)
        result = await self._session.execute(
            stmt.order_by(ReviewModel.created_at.desc()))
        return [self._review_to_domain(r) for r in result.scalars().all()]

    async def update_review(self, review: Review) -> Review:
        row = (await self._session.execute(
            select(ReviewModel).where(ReviewModel.id == review.id)
        )).scalar_one()
        for field in ("kind", "title", "body", "source_head", "target_base",
                      "state", "merged_at", "merged_by", "merged_revision_id",
                      "self_merged"):
            setattr(row, field, getattr(review, field))
        await self._session.commit()
        await self._session.refresh(row)
        return self._review_to_domain(row)

    async def reviews_for_user(self, user_id: str) -> list[Review]:
        """Authored or invited — one list, because that is the question
        being asked: what is waiting for me?"""
        invited = select(ReviewReviewerModel.review_id).where(
            ReviewReviewerModel.user_id == user_id)
        result = await self._session.execute(
            select(ReviewModel)
            .where(or_(ReviewModel.author_id == user_id,
                       ReviewModel.id.in_(invited)))
            .order_by(ReviewModel.updated_at.desc())
        )
        return [self._review_to_domain(r) for r in result.scalars().all()]

    async def add_reviewer(self, reviewer: ReviewReviewer) -> ReviewReviewer:
        existing = (await self._session.execute(
            select(ReviewReviewerModel).where(
                ReviewReviewerModel.review_id == reviewer.review_id,
                ReviewReviewerModel.user_id == reviewer.user_id)
        )).scalar_one_or_none()
        if existing is not None:
            return reviewer
        self._session.add(ReviewReviewerModel(
            review_id=reviewer.review_id, user_id=reviewer.user_id,
            invited_by=reviewer.invited_by,
        ))
        await self._session.commit()
        return reviewer

    async def list_reviewers(self, review_id: str) -> list[ReviewReviewer]:
        result = await self._session.execute(
            select(ReviewReviewerModel)
            .where(ReviewReviewerModel.review_id == review_id)
            .order_by(ReviewReviewerModel.invited_at)
        )
        return [ReviewReviewer(review_id=r.review_id, user_id=r.user_id,
                               invited_by=r.invited_by, invited_at=r.invited_at)
                for r in result.scalars().all()]

    @staticmethod
    def _comment_to_domain(row: ReviewCommentModel) -> ReviewComment:
        return ReviewComment(
            id=row.id, review_id=row.review_id, author_id=row.author_id,
            anchor=row.anchor, body=row.body, resolved=row.resolved,
            created_at=row.created_at,
        )

    async def add_review_comment(self, comment: ReviewComment) -> ReviewComment:
        model = ReviewCommentModel(
            review_id=comment.review_id, author_id=comment.author_id,
            anchor=comment.anchor, body=comment.body,
            resolved=comment.resolved,
        )
        self._session.add(model)
        await self._session.commit()
        await self._session.refresh(model)
        return self._comment_to_domain(model)

    async def list_review_comments(self, review_id: str) -> list[ReviewComment]:
        result = await self._session.execute(
            select(ReviewCommentModel)
            .where(ReviewCommentModel.review_id == review_id)
            .order_by(ReviewCommentModel.created_at)
        )
        return [self._comment_to_domain(r) for r in result.scalars().all()]

    async def get_review_comment(self, comment_id: str) -> ReviewComment | None:
        row = (await self._session.execute(
            select(ReviewCommentModel).where(ReviewCommentModel.id == comment_id)
        )).scalar_one_or_none()
        return self._comment_to_domain(row) if row else None

    async def update_review_comment(self, comment: ReviewComment) -> ReviewComment:
        row = (await self._session.execute(
            select(ReviewCommentModel).where(ReviewCommentModel.id == comment.id)
        )).scalar_one()
        row.body = comment.body
        row.resolved = comment.resolved
        await self._session.commit()
        await self._session.refresh(row)
        return self._comment_to_domain(row)

    async def save_version(self, section_id: str, content: dict, user_id: str) -> None:
        model = SectionVersionModel(
            section_id=section_id,
            content_json=content,
            saved_by=user_id,
            saved_at=datetime.now(timezone.utc),
        )
        self._session.add(model)
        await self._session.commit()

    async def get_versions(self, section_id: str, limit: int) -> list[SectionVersion]:
        result = await self._session.execute(
            select(SectionVersionModel)
            .where(SectionVersionModel.section_id == section_id)
            .order_by(SectionVersionModel.saved_at.desc())
            .limit(limit)
        )
        return [self._version_to_domain(r) for r in result.scalars().all()]

    async def set_dossier(
        self, report_id: str, dossier_id: str | None, parent_id: str | None,
    ) -> None:
        await self._session.execute(
            ReportModel.__table__.update()
            .where(ReportModel.id == report_id)
            .values(dossier_id=dossier_id, parent_id=parent_id)
        )
        await self._session.commit()

    async def list_by_dossier(self, dossier_id: str) -> list[Report]:
        result = await self._session.execute(
            select(ReportModel).where(ReportModel.dossier_id == dossier_id)
        )
        return [self._report_to_domain(r) for r in result.scalars().all()]

    async def set_investigation(self, report_id: str, investigation_id: str | None) -> None:
        await self._session.execute(
            ReportModel.__table__.update()
            .where(ReportModel.id == report_id)
            .values(investigation_id=investigation_id)
        )
        await self._session.commit()

    async def list_by_investigation(self, investigation_id: str) -> list[Report]:
        result = await self._session.execute(
            select(ReportModel).where(ReportModel.investigation_id == investigation_id)
        )
        return [self._report_to_domain(r) for r in result.scalars().all()]

    async def list_children(self, parent_id: str) -> list[Report]:
        result = await self._session.execute(
            select(ReportModel)
            .where(ReportModel.parent_id == parent_id)
            .order_by(ReportModel.created_at)
        )
        return [self._report_to_domain(r) for r in result.scalars().all()]

    @staticmethod
    def _translation_to_domain(row: ReportTranslationModel) -> ReportTranslation:
        return ReportTranslation(
            id=row.id,
            report_id=row.report_id,
            lang=row.lang,
            title=row.title,
            abstract=row.abstract,
            content_json=row.content_json or {},
            source_version=row.source_version,
            created_by=row.created_by,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    async def get_translation(self, report_id: str, lang: str) -> ReportTranslation | None:
        result = await self._session.execute(
            select(ReportTranslationModel).where(
                ReportTranslationModel.report_id == report_id,
                ReportTranslationModel.lang == lang,
            )
        )
        row = result.scalar_one_or_none()
        return self._translation_to_domain(row) if row else None

    async def list_translations(self, report_id: str) -> list[ReportTranslation]:
        result = await self._session.execute(
            select(ReportTranslationModel)
            .where(ReportTranslationModel.report_id == report_id)
            .order_by(ReportTranslationModel.lang)
        )
        return [self._translation_to_domain(r) for r in result.scalars().all()]

    async def upsert_translation(self, translation: ReportTranslation) -> ReportTranslation:
        result = await self._session.execute(
            select(ReportTranslationModel).where(
                ReportTranslationModel.report_id == translation.report_id,
                ReportTranslationModel.lang == translation.lang,
            )
        )
        row = result.scalar_one_or_none()
        now = datetime.now(timezone.utc)
        if row is None:
            row = ReportTranslationModel(
                id=translation.id or str(uuid4()),
                report_id=translation.report_id,
                lang=translation.lang,
                title=translation.title,
                abstract=translation.abstract,
                content_json=translation.content_json,
                source_version=translation.source_version,
                created_by=translation.created_by,
                created_at=now,
                updated_at=now,
            )
            self._session.add(row)
        else:
            row.title = translation.title
            row.abstract = translation.abstract
            row.content_json = translation.content_json
            row.source_version = translation.source_version
            row.updated_at = now
        await self._session.commit()
        return self._translation_to_domain(row)

    async def delete_translation(self, report_id: str, lang: str) -> None:
        await self._session.execute(
            delete(ReportTranslationModel).where(
                ReportTranslationModel.report_id == report_id,
                ReportTranslationModel.lang == lang,
            )
        )
        await self._session.commit()

    async def get_translation_summaries(
        self, report_ids: list[str], lang: str
    ) -> list[ReportTranslation]:
        if not report_ids:
            return []
        result = await self._session.execute(
            select(ReportTranslationModel).where(
                ReportTranslationModel.report_id.in_(report_ids),
                ReportTranslationModel.lang == lang,
            )
        )
        return [self._translation_to_domain(r) for r in result.scalars().all()]
