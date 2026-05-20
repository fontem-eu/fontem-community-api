from __future__ import annotations

from abc import ABC, abstractmethod


class TagFollowRepository(ABC):
    """User → followed tags. Used by signed-in callers; unauth
    callers persist their follow set in localStorage on the client.
    """

    @abstractmethod
    async def list(self, user_id: str) -> list[str]: ...

    @abstractmethod
    async def follow(self, user_id: str, tag: str) -> None:
        """Idempotent. Service layer enforces the ≤50-per-user cap
        before calling; the repo treats it as a plain insert (with
        ON CONFLICT DO NOTHING for races)."""

    @abstractmethod
    async def unfollow(self, user_id: str, tag: str) -> None: ...

    @abstractmethod
    async def count(self, user_id: str) -> int:
        """Used by the service layer to enforce the 50-tag follow cap."""
