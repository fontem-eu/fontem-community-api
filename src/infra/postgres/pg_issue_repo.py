from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.issue import Comment, Issue
from src.infra.postgres.models import CommentModel, IssueModel, IssueVoteModel
from src.repositories.issue_repository import IssueRepository


class PgIssueRepository(IssueRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _issue_to_domain(row: IssueModel) -> Issue:
        return Issue(
            id=row.id,
            title=row.title,
            body_md=row.body_md,
            issue_type=row.issue_type,
            entity_type=row.entity_type,
            entity_id=row.entity_id,
            status=row.status,
            created_by=row.created_by,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _comment_to_domain(row: CommentModel) -> Comment:
        return Comment(
            id=row.id,
            parent_type=row.parent_type,
            parent_id=row.parent_id,
            body_md=row.body_md,
            author_id=row.author_id,
            created_at=row.created_at,
        )

    async def create(self, issue: Issue) -> Issue:
        now = datetime.now(timezone.utc)
        model = IssueModel(
            id=issue.id or str(uuid4()),
            title=issue.title,
            body_md=issue.body_md,
            issue_type=issue.issue_type,
            entity_type=issue.entity_type,
            entity_id=issue.entity_id,
            status=issue.status,
            created_by=issue.created_by,
            created_at=issue.created_at or now,
            updated_at=issue.updated_at or now,
        )
        self._session.add(model)
        await self._session.commit()
        return self._issue_to_domain(model)

    async def get_by_id(self, issue_id: str) -> Issue | None:
        result = await self._session.execute(
            select(IssueModel).where(IssueModel.id == issue_id)
        )
        row = result.scalar_one_or_none()
        return self._issue_to_domain(row) if row else None

    async def update_status(self, issue_id: str, status: str) -> None:
        result = await self._session.execute(
            select(IssueModel).where(IssueModel.id == issue_id)
        )
        row = result.scalar_one_or_none()
        if row is not None:
            row.status = status
            row.updated_at = datetime.now(timezone.utc)
            await self._session.commit()

    async def list_for_entity(
        self, entity_type: str, entity_id: str, limit: int, offset: int
    ) -> list[Issue]:
        result = await self._session.execute(
            select(IssueModel)
            .where(IssueModel.entity_type == entity_type)
            .where(IssueModel.entity_id == entity_id)
            .order_by(IssueModel.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return [self._issue_to_domain(r) for r in result.scalars().all()]

    async def list_open(self, limit: int, offset: int) -> list[Issue]:
        result = await self._session.execute(
            select(IssueModel)
            .where(IssueModel.status == "open")
            .order_by(IssueModel.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return [self._issue_to_domain(r) for r in result.scalars().all()]

    async def add_comment(self, comment: Comment) -> Comment:
        now = datetime.now(timezone.utc)
        model = CommentModel(
            id=comment.id or str(uuid4()),
            parent_type=comment.parent_type,
            parent_id=comment.parent_id,
            body_md=comment.body_md,
            author_id=comment.author_id,
            created_at=comment.created_at or now,
        )
        self._session.add(model)
        await self._session.commit()
        return self._comment_to_domain(model)

    async def get_comments(self, parent_type: str, parent_id: str) -> list[Comment]:
        result = await self._session.execute(
            select(CommentModel)
            .where(CommentModel.parent_type == parent_type)
            .where(CommentModel.parent_id == parent_id)
            .order_by(CommentModel.created_at.asc())
        )
        return [self._comment_to_domain(r) for r in result.scalars().all()]

    async def vote(self, issue_id: str, user_id: str, direction: str) -> None:
        stmt = pg_insert(IssueVoteModel).values(
            issue_id=issue_id,
            user_id=user_id,
            direction=direction,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["issue_id", "user_id"],
            set_={"direction": stmt.excluded.direction},
        )
        await self._session.execute(stmt)
        await self._session.commit()

    async def get_vote_count(self, issue_id: str) -> int:
        up_q = (
            select(func.count())
            .select_from(IssueVoteModel)
            .where(IssueVoteModel.issue_id == issue_id)
            .where(IssueVoteModel.direction == "up")
        )
        down_q = (
            select(func.count())
            .select_from(IssueVoteModel)
            .where(IssueVoteModel.issue_id == issue_id)
            .where(IssueVoteModel.direction == "down")
        )
        up_result = await self._session.execute(up_q)
        down_result = await self._session.execute(down_q)
        up_count = up_result.scalar_one()
        down_count = down_result.scalar_one()
        return up_count - down_count
