"""Repository ABC for the admin user directory (read-only)."""
from __future__ import annotations

from abc import ABC, abstractmethod

from src.domain.user_directory import UserDirectoryEntry


class UserDirectoryRepository(ABC):
    @abstractmethod
    async def list_users(
        self, *, sort: str, limit: int, offset: int,
    ) -> tuple[list[UserDirectoryEntry], int]:
        """One page of accounts, and how many accounts there are in total.

        ``sort`` is one of :data:`src.domain.user_directory.DIRECTORY_SORTS`.
        Every order is descending with missing values last, then by id.
        """
