from __future__ import annotations

from copy import deepcopy

from src.domain.user_profile import UserProfile
from src.repositories.user_profile_repository import UserProfileRepository


class InMemoryUserProfileRepository(UserProfileRepository):
    def __init__(self) -> None:
        self._by_user: dict[str, UserProfile] = {}

    async def get(self, user_id: str) -> UserProfile | None:
        p = self._by_user.get(user_id)
        return deepcopy(p) if p is not None else None

    async def upsert(self, profile: UserProfile) -> UserProfile:
        self._by_user[profile.user_id] = deepcopy(profile)
        return deepcopy(profile)
