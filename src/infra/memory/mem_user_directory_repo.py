"""The admin user directory over the in-memory repositories.

Mirrors PgUserDirectoryRepository exactly — descending with missing values
last, ties broken by id — so the unit tests hold the same contract the SQL
is integration-tested against.
"""
from __future__ import annotations

from src.domain.user_directory import DIRECTORY_SORTS, UserDirectoryEntry
from src.infra.memory.mem_activity_repo import InMemoryActivityRepository
from src.infra.memory.mem_refresh_token_repo import InMemoryRefreshTokenRepository
from src.infra.memory.mem_user_repo import InMemoryUserRepository
from src.repositories.user_directory_repository import UserDirectoryRepository

_SORT_FIELD = {
    "registered": "registered_at",
    "last_login": "last_login_at",
    "last_seen": "last_seen_at",
    "last_activity": "last_activity_at",
    "activity_count": "activity_count",
}


class InMemoryUserDirectoryRepository(UserDirectoryRepository):
    def __init__(
        self,
        users: InMemoryUserRepository,
        activity: InMemoryActivityRepository,
        refresh_tokens: InMemoryRefreshTokenRepository,
    ) -> None:
        self._users = users
        self._activity = activity
        self._refresh_tokens = refresh_tokens

    async def list_users(
        self, *, sort: str, limit: int, offset: int,
    ) -> tuple[list[UserDirectoryEntry], int]:
        if sort not in DIRECTORY_SORTS:
            raise ValueError(f"unknown directory sort {sort!r}")
        events = self._activity.all_events()
        families = self._refresh_tokens.all_families()

        entries = []
        for user in self._users.all_users():
            acted = [e.created_at for e in events if e.actor_id == user.id and e.created_at]
            seen = [f.rotated_at for f in families if f.user_id == user.id and f.rotated_at]
            entries.append(UserDirectoryEntry(
                id=user.id,
                email=user.email,
                name=user.name,
                trust_level=user.trust_level,
                registered_at=user.created_at,
                email_verified=user.email_verified_at is not None,
                last_login_at=user.last_login_at,
                last_seen_at=max(seen) if seen else None,
                last_activity_at=max(acted) if acted else None,
                activity_count=sum(1 for e in events if e.actor_id == user.id),
                roles=sorted(await self._users.get_roles(user.id)),
            ))

        # Descending, missing values last, ties by id: sort by id first, then
        # stably by value, which is exactly ORDER BY value DESC NULLS LAST, id.
        name = _SORT_FIELD[sort]
        entries.sort(key=lambda e: e.id)
        present = [e for e in entries if getattr(e, name) is not None]
        missing = [e for e in entries if getattr(e, name) is None]
        present.sort(key=lambda e: getattr(e, name), reverse=True)
        ordered = present + missing
        return ordered[offset:offset + limit], len(ordered)
