from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4

from src.domain.moderation import Sanction
from src.domain.user import User
from src.repositories.user_repository import UserRepository


class InMemoryUserRepository(UserRepository):
    def __init__(self) -> None:
        self._users: dict[str, User] = {}
        self._roles: dict[str, list[str]] = {}
        self._sanctions: dict[str, list[Sanction]] = {}

    async def get_by_id(self, user_id: str) -> User | None:
        user = self._users.get(user_id)
        return deepcopy(user) if user else None

    async def get_by_email(self, email: str) -> User | None:
        for user in self._users.values():
            if user.email == email:
                return deepcopy(user)
        return None

    async def upsert(self, user: User) -> User:
        if user.id is None:
            user.id = str(uuid4())
        if user.created_at is None:
            existing = self._users.get(user.id)
            user.created_at = existing.created_at if existing else datetime.now(timezone.utc)
        self._users[user.id] = deepcopy(user)
        return deepcopy(user)

    async def get_roles(self, user_id: str) -> list[str]:
        return list(self._roles.get(user_id, []))

    async def set_roles(self, user_id: str, roles: list[str]) -> None:
        self._roles[user_id] = list(roles)

    async def set_trust_level(self, user_id: str, level: str) -> None:
        user = self._users.get(user_id)
        if user:
            user.trust_level = level

    async def get_active_sanction(self, user_id: str) -> Sanction | None:
        now = datetime.now(timezone.utc)
        for sanction in self._sanctions.get(user_id, []):
            if sanction.lifted_at is not None:
                continue
            if sanction.expires_at is not None and sanction.expires_at < now:
                continue
            return deepcopy(sanction)
        return None

    def add_sanction_sync(self, sanction: Sanction) -> None:
        """Add a sanction directly (used by moderation service and tests)."""
        self._sanctions.setdefault(sanction.user_id, []).append(deepcopy(sanction))
