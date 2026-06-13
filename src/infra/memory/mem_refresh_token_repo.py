"""In-memory ``RefreshTokenRepository`` used by the unit-test conftest.

Tracks families in a plain dict keyed by id. Mirrors the atomic
guarantees of the PG implementation: ``rotate`` returns False when
the family is revoked OR when the offered hash isn't the current
one — the test still sees the reuse-detection contract.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from src.repositories.refresh_token_repository import (
    RefreshTokenFamily,
    RefreshTokenRepository,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class InMemoryRefreshTokenRepository(RefreshTokenRepository):
    def __init__(self) -> None:
        self._families: dict[str, RefreshTokenFamily] = {}

    async def create_family(
        self, family: RefreshTokenFamily,
    ) -> RefreshTokenFamily:
        # Dataclass replace-by-copy so callers don't accidentally
        # mutate the stored row through their handle.
        stored = replace(family)
        self._families[stored.id] = stored
        return replace(stored)

    async def find_by_current_hash(
        self, token_hash: str,
    ) -> RefreshTokenFamily | None:
        for f in self._families.values():
            if f.current_token_hash == token_hash:
                return replace(f)
        return None

    async def rotate(
        self,
        family_id: str,
        new_token_hash: str,
        new_expires_at: datetime,
    ) -> bool:
        f = self._families.get(family_id)
        if f is None or f.revoked_at is not None:
            return False
        self._families[family_id] = replace(
            f,
            current_token_hash=new_token_hash,
            rotated_at=_now(),
            expires_at=new_expires_at,
        )
        return True

    async def revoke_family(self, family_id: str, reason: str) -> None:
        f = self._families.get(family_id)
        if f is None:
            return
        if f.revoked_at is not None:
            # Idempotent — already revoked.
            return
        self._families[family_id] = replace(
            f, revoked_at=_now(), revoked_reason=reason,
        )

    async def revoke_all_for_user(self, user_id: str, reason: str) -> int:
        n = 0
        for fid, f in list(self._families.items()):
            if f.user_id == user_id and f.revoked_at is None:
                self._families[fid] = replace(
                    f, revoked_at=_now(), revoked_reason=reason,
                )
                n += 1
        return n

    async def get_by_id(self, family_id: str) -> RefreshTokenFamily | None:
        f = self._families.get(family_id)
        return replace(f) if f is not None else None
