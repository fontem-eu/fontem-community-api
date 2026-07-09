from __future__ import annotations

from abc import ABC, abstractmethod

from src.domain.user_profile import UserProfile


class UserProfileRepository(ABC):
    """Persistence for a user's editable profile extras (summary + links)."""

    @abstractmethod
    async def get(self, user_id: str) -> UserProfile | None: ...

    @abstractmethod
    async def upsert(self, profile: UserProfile) -> UserProfile:
        """Create or replace the profile row for ``profile.user_id``."""
