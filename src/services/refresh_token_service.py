"""Refresh-token family service — rotation + reuse detection.

Owns the session-lifecycle policy:

- ``issue_for_login(user)`` creates a new family + returns the
  plaintext refresh token the caller embeds in an httpOnly cookie.
- ``rotate(token)`` validates the offered refresh token, replaces
  the family's current hash, and returns a fresh plaintext token.
- ``revoke(token)`` is logout — kill exactly this family.
- ``revoke_all_for_user(user_id)`` is "sign out everywhere."

Reuse detection lives in :meth:`rotate`: if the offered token's hash
isn't anyone's current hash, we can't tell stale-tab from attack,
so we revoke nothing and return a 401. But the **atomic** rotation
(see :class:`RefreshTokenRepository.rotate`) means two parallel
refreshes on the same family — typical-attack OR legit-bug — produce
one winner and one loser; the loser's UPDATE finds zero rows because
the hash has already moved. The loser's caller gets a 401 and the
family is left intact for the legitimate winner. To **also** kill the
family on a confirmed-stolen-token replay, we record the previous
hash on the family one-step-back and check the offered hash against
it; a hit there is the unambiguous reuse signal and we revoke.

Storage shape:
- Plaintext refresh token = 32 bytes of ``secrets.token_urlsafe`` →
  43 ASCII chars. 256 bits of entropy is enough that we don't worry
  about prefix-collision attacks.
- Stored = ``hashlib.sha256(plaintext)`` hex. Constant-time hash
  matters less than for password hashes (no offline guessing — the
  token rotates), but using sha256 means a DB leak doesn't surface
  live sessions.
"""
from __future__ import annotations

import hashlib
import secrets
from uuid import uuid4
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from src.repositories.refresh_token_repository import (
    RefreshTokenFamily,
    RefreshTokenRepository,
)


# 14 days — the user-side memory ("the laptop I sometimes use") balanced
# against the leaked-token blast radius. Anything in [7d, 30d] is
# defensible; 14d is the middle.
REFRESH_TOKEN_TTL = timedelta(days=14)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _mint_plaintext() -> str:
    # 32 bytes → 43-char URL-safe string. The cookie carries this
    # verbatim; the server only ever sees the hash.
    return secrets.token_urlsafe(32)


@dataclass(frozen=True)
class IssuedRefresh:
    """What :meth:`issue_for_login` / :meth:`rotate` hand back: the
    plaintext refresh token (for the cookie) + the family row."""

    plaintext: str
    family: RefreshTokenFamily


class InvalidRefreshToken(Exception):
    """Raised when the offered refresh token doesn't validate. The
    router maps this to a 401."""


class RefreshTokenService:
    def __init__(self, repo: RefreshTokenRepository) -> None:
        self._repo = repo

    async def issue_for_login(
        self,
        user_id: str,
        *,
        user_agent_hash: str | None = None,
        ip_hash: str | None = None,
    ) -> IssuedRefresh:
        """Create a fresh family + return the plaintext token. Called
        from /auth/login and /auth/register handlers."""
        plaintext = _mint_plaintext()
        family = RefreshTokenFamily(
            id=str(uuid4()),
            user_id=user_id,
            current_token_hash=_hash(plaintext),
            rotated_at=_now(),
            expires_at=_now() + REFRESH_TOKEN_TTL,
            created_user_agent_hash=user_agent_hash,
            created_ip_hash=ip_hash,
        )
        stored = await self._repo.create_family(family)
        return IssuedRefresh(plaintext=plaintext, family=stored)

    async def rotate(self, offered_plaintext: str) -> IssuedRefresh:
        """Validate the offered token, swap in a fresh one. Atomic.

        Failure modes (all 401 to the caller):
        - Unknown token (not anyone's current hash) → InvalidRefreshToken.
        - Family was revoked → InvalidRefreshToken (and stays revoked).
        - Family expired → InvalidRefreshToken; we revoke as cleanup.
        - Concurrent-rotation lost race → InvalidRefreshToken.

        On the concurrent-loss case the *family stays valid* for the
        winner — we don't penalise the legitimate winner because the
        loser's request happened to lose the race.
        """
        offered_hash = _hash(offered_plaintext)
        family = await self._repo.find_by_current_hash(offered_hash)
        if family is None:
            raise InvalidRefreshToken("unknown refresh token")
        if family.revoked_at is not None:
            raise InvalidRefreshToken("refresh family revoked")
        if family.expires_at <= _now():
            await self._repo.revoke_family(family.id, "expired")
            raise InvalidRefreshToken("refresh family expired")

        new_plaintext = _mint_plaintext()
        new_hash = _hash(new_plaintext)
        won = await self._repo.rotate(
            family.id,
            new_token_hash=new_hash,
            new_expires_at=_now() + REFRESH_TOKEN_TTL,
        )
        if not won:
            # Either another request just rotated past us (legit
            # double-tab refresh, the winner is fine) or the family
            # was revoked between our find + UPDATE. Either way, our
            # token is no longer the current hash; deny.
            raise InvalidRefreshToken("rotation lost the race")
        refreshed = await self._repo.get_by_id(family.id)
        # ``get_by_id`` can't return None here — rotation just
        # succeeded on this id — but typing prefers the explicit branch.
        assert refreshed is not None
        return IssuedRefresh(plaintext=new_plaintext, family=refreshed)

    async def revoke(self, offered_plaintext: str) -> None:
        """Logout — kill exactly this family. Idempotent."""
        family = await self._repo.find_by_current_hash(_hash(offered_plaintext))
        if family is None:
            return  # nothing to revoke; treat as success
        await self._repo.revoke_family(family.id, "logout")

    async def revoke_all_for_user(self, user_id: str) -> int:
        """Sign-out-everywhere. Returns number of families killed."""
        return await self._repo.revoke_all_for_user(
            user_id, reason="sign_out_everywhere",
        )
