from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.user_profile import ProfileLink, UserProfile
from src.infra.postgres.models import UserProfileModel
from src.repositories.user_profile_repository import UserProfileRepository


class PgUserProfileRepository(UserProfileRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _to_domain(row: UserProfileModel) -> UserProfile:
        return UserProfile(
            user_id=row.user_id,
            summary=row.summary or "",
            links=[ProfileLink(name=l.get("name", ""), url=l.get("url", ""))
                   for l in (row.links or [])],
            avatar_x=row.avatar_x if row.avatar_x is not None else 50.0,
            avatar_y=row.avatar_y if row.avatar_y is not None else 50.0,
            show_email=bool(row.show_email),
            use_custom_email=bool(row.use_custom_email),
            custom_email=row.custom_email or "",
            home_nuts=row.home_nuts or "",
            updated_at=row.updated_at,
        )

    async def get(self, user_id: str) -> UserProfile | None:
        row = await self._session.get(UserProfileModel, user_id)
        return self._to_domain(row) if row is not None else None

    async def upsert(self, profile: UserProfile) -> UserProfile:
        links = [{"name": l.name, "url": l.url} for l in profile.links]
        now = datetime.now(timezone.utc)
        row = await self._session.get(UserProfileModel, profile.user_id)
        if row is None:
            row = UserProfileModel(user_id=profile.user_id)
            self._session.add(row)
        row.summary = profile.summary or ""
        row.links = links
        row.avatar_x = profile.avatar_x
        row.avatar_y = profile.avatar_y
        row.show_email = profile.show_email
        row.use_custom_email = profile.use_custom_email
        row.custom_email = profile.custom_email or ""
        row.home_nuts = profile.home_nuts or ""
        row.updated_at = now
        await self._session.flush()
        return self._to_domain(row)
