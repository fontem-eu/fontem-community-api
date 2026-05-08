from __future__ import annotations

from src.repositories.tag_follow_repository import TagFollowRepository


class InMemoryTagFollowRepository(TagFollowRepository):
    def __init__(self) -> None:
        self._by_user: dict[str, set[str]] = {}

    async def list(self, user_id: str) -> list[str]:
        return sorted(self._by_user.get(user_id, set()))

    async def follow(self, user_id: str, tag: str) -> None:
        self._by_user.setdefault(user_id, set()).add(tag)

    async def unfollow(self, user_id: str, tag: str) -> None:
        self._by_user.get(user_id, set()).discard(tag)

    async def count(self, user_id: str) -> int:
        return len(self._by_user.get(user_id, set()))
