"""The admin user directory, in one query.

Three per-account aggregates are joined onto ``users`` as grouped
subqueries: activity (count and most recent, served by
ix_activity_log_actor), sessions (most recent token rotation, served by
ix_refresh_token_families_user_id) and roles. Grouping each one before the
join is what keeps them independent — joining the raw tables would
multiply an account's activity count by its session and role counts.
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.user_directory import DIRECTORY_SORTS, UserDirectoryEntry
from src.infra.postgres.models import (
    ActivityLogModel,
    RefreshTokenFamilyModel,
    UserModel,
    UserRoleModel,
)
from src.repositories.user_directory_repository import UserDirectoryRepository

# Labels shared by the subqueries and the outer select.
_USER_ID = "user_id"
_ACTIVITY_COUNT = "activity_count"

# SQLAlchemy generates `func.<name>` at runtime, so pylint can see neither
# that it is callable nor that it returns. The disables below are scoped to
# exactly those lines.


class PgUserDirectoryRepository(UserDirectoryRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_users(
        self, *, sort: str, limit: int, offset: int,
    ) -> tuple[list[UserDirectoryEntry], int]:
        if sort not in DIRECTORY_SORTS:
            raise ValueError(f"unknown directory sort {sort!r}")

        activity = (
            select(
                ActivityLogModel.actor_id.label(_USER_ID),
                func.count().label(_ACTIVITY_COUNT),  # pylint: disable=not-callable
                func.max(ActivityLogModel.created_at).label("last_activity_at"),
            )
            .group_by(ActivityLogModel.actor_id)
            .subquery()
        )
        sessions = (
            select(
                RefreshTokenFamilyModel.user_id.label(_USER_ID),
                func.max(RefreshTokenFamilyModel.rotated_at).label("last_seen_at"),
            )
            .group_by(RefreshTokenFamilyModel.user_id)
            .subquery()
        )
        roles = (
            select(
                UserRoleModel.user_id.label(_USER_ID),
                func.array_agg(
                    aggregate_order_by(UserRoleModel.role, UserRoleModel.role),
                ).label("roles"),
            )
            .group_by(UserRoleModel.user_id)
            .subquery()
        )
        activity_count = func.coalesce(activity.c.activity_count, 0)  # pylint: disable=assignment-from-no-return
        order_column = {
            "registered": UserModel.created_at,
            "last_login": UserModel.last_login_at,
            "last_seen": sessions.c.last_seen_at,
            "last_activity": activity.c.last_activity_at,
            "activity_count": activity_count,
        }[sort]

        stmt = (
            select(
                UserModel.id,
                UserModel.email,
                UserModel.name,
                UserModel.trust_level,
                UserModel.created_at,
                UserModel.email_verified_at,
                UserModel.last_login_at,
                sessions.c.last_seen_at,
                activity.c.last_activity_at,
                activity_count.label(_ACTIVITY_COUNT),
                roles.c.roles,
            )
            .select_from(UserModel)
            .outerjoin(activity, activity.c.user_id == UserModel.id)
            .outerjoin(sessions, sessions.c.user_id == UserModel.id)
            .outerjoin(roles, roles.c.user_id == UserModel.id)
            .order_by(order_column.desc().nulls_last(), UserModel.id)
            .limit(limit)
            .offset(offset)
        )
        rows = (await self._session.execute(stmt)).all()
        total = await self._session.scalar(
            select(func.count()).select_from(UserModel),  # pylint: disable=not-callable
        )
        return [
            UserDirectoryEntry(
                id=row.id,
                email=row.email,
                name=row.name,
                trust_level=row.trust_level,
                registered_at=row.created_at,
                email_verified=row.email_verified_at is not None,
                last_login_at=row.last_login_at,
                last_seen_at=row.last_seen_at,
                last_activity_at=row.last_activity_at,
                activity_count=int(row.activity_count),
                roles=list(row.roles or []),
            )
            for row in rows
        ], int(total or 0)
