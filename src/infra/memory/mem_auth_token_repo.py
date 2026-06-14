"""In-memory ``AuthTokenRepository`` for the unit-test conftest."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from src.repositories.auth_token_repository import AuthToken, AuthTokenRepository


def _now() -> datetime:
    return datetime.now(timezone.utc)


class InMemoryAuthTokenRepository(AuthTokenRepository):
    def __init__(self) -> None:
        self._tokens: dict[str, AuthToken] = {}

    async def create(self, token: AuthToken) -> AuthToken:
        stored = replace(token, created_at=token.created_at or _now())
        self._tokens[stored.id] = stored
        return replace(stored)

    async def consume(self, token_hash: str, purpose: str, now: datetime) -> AuthToken | None:
        for tid, tok in self._tokens.items():
            if (
                tok.token_hash == token_hash
                and tok.purpose == purpose
                and tok.consumed_at is None
                and tok.expires_at > now
            ):
                self._tokens[tid] = replace(tok, consumed_at=now)
                return replace(self._tokens[tid])
        return None

    async def invalidate_outstanding(self, user_id: str, purpose: str) -> int:
        n = 0
        for tid, tok in list(self._tokens.items()):
            if tok.user_id == user_id and tok.purpose == purpose and tok.consumed_at is None:
                self._tokens[tid] = replace(tok, consumed_at=_now())
                n += 1
        return n
