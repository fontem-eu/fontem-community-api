"""Repository ABC for single-use auth tokens (email verify + password reset).

Tokens are stored as SHA-256 hashes; plaintext never lands in the DB.
A token validates iff a row exists with the matching hash + purpose,
``consumed_at IS NULL``, and ``expires_at`` in the future. ``consume``
is atomic so a token can't be redeemed twice in a race.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass
class AuthToken:
    id: str
    user_id: str
    token_hash: str
    purpose: str  # 'verify_email' | 'password_reset'
    expires_at: datetime
    consumed_at: datetime | None = None
    created_at: datetime | None = None


class AuthTokenRepository(ABC):
    @abstractmethod
    async def create(self, token: AuthToken) -> AuthToken:
        """Persist a new token row."""
        ...

    @abstractmethod
    async def consume(self, token_hash: str, purpose: str, now: datetime) -> AuthToken | None:
        """Atomically mark the matching live token consumed and return it.

        Returns ``None`` when no live token matches (unknown hash,
        wrong purpose, already consumed, or expired). The match +
        mark-consumed happen in one statement so two concurrent
        redeems produce exactly one winner.
        """
        ...

    @abstractmethod
    async def invalidate_outstanding(self, user_id: str, purpose: str) -> int:
        """Consume every still-live token of ``purpose`` for the user.

        Used when issuing a fresh token so an account only ever has
        one live verification / reset link at a time (clicking an old
        emailed link after requesting a new one should fail). Returns
        the count invalidated.
        """
        ...
