"""Repository ABC for refresh-token families.

A *family* is one continuous login session: created at login, rotated
on every refresh, killed on logout or refresh-token-reuse detection.
The repo deals in token *hashes* — plaintext refresh tokens never
land in the DB so a dump doesn't hand attackers live sessions.

Reuse detection is the novel security property: the repo exposes a
``find_by_hash`` that the service uses to look up the family the
caller's token *would* belong to; if the hash isn't the family's
``current_token_hash`` but matches *some* prior family's history,
the service revokes that family. We don't keep per-token history
rows — instead the service compares the offered hash against the
family's current hash; mismatch on a family the caller can prove
they used to own (by user_id correlation) is the reuse signal.

The simpler shape: the service offers (current_token_hash,
user_id_hint) and the repo answers with the family. Any token that
isn't the *current* hash but matches a known family-by-user is
either a stale tab or a real attack — the service revokes either
way; legitimate stale tabs need to log in again, a small price for
killing reused-token attacks dead.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass
# pylint: disable-next=too-many-instance-attributes
class RefreshTokenFamily:
    """Domain representation of one login session."""

    id: str
    user_id: str
    current_token_hash: str
    rotated_at: datetime
    #: The hash this family carried before its last rotation, or None.
    expires_at: datetime
    revoked_at: datetime | None = None
    revoked_reason: str | None = None
    created_user_agent_hash: str | None = None
    created_ip_hash: str | None = None
    previous_token_hash: str | None = None


class RefreshTokenRepository(ABC):
    @abstractmethod
    async def create_family(self, family: RefreshTokenFamily) -> RefreshTokenFamily:
        """Persist a new family. Returns the persisted row."""
        ...

    @abstractmethod
    async def find_by_previous_hash(
        self, token_hash: str,  # pylint: disable=unused-argument
    ) -> RefreshTokenFamily | None:
        """The family whose PREVIOUS hash this was, if any.

        Used to tell a concurrent refresh from a replayed token: the same
        lookup answers both questions, and `rotated_at` decides which.
        """
        ...

    async def find_by_current_hash(self, token_hash: str) -> RefreshTokenFamily | None:
        """Return the family whose **current** token hash matches.

        Returns ``None`` if no family has this as its live token. The
        caller distinguishes the two reuse-signal cases (stale vs
        attack) at the service layer."""
        ...

    @abstractmethod
    async def rotate(
        self,
        family_id: str,
        new_token_hash: str,
        new_expires_at: datetime,
    ) -> bool:
        """Atomically replace the family's ``current_token_hash`` and
        bump ``rotated_at`` / ``expires_at``.

        The atomic guarantee: two concurrent refreshes on the same
        family see exactly one ``True``; the loser sees ``False`` and
        the service treats that as reuse (because by then *someone*
        rotated past their copy).

        Implementations enforce this with a WHERE clause matching the
        old hash + ``revoked_at IS NULL``; the loser's UPDATE finds
        0 rows because the hash already moved.
        """
        ...

    @abstractmethod
    async def revoke_family(self, family_id: str, reason: str) -> None:
        """Mark a family revoked. Idempotent — re-revoking a revoked
        family is fine."""
        ...

    @abstractmethod
    async def revoke_all_for_user(self, user_id: str, reason: str) -> int:
        """Revoke every non-revoked family for a user. Returns count
        affected. Used by "sign out everywhere"."""
        ...

    @abstractmethod
    async def get_by_id(self, family_id: str) -> RefreshTokenFamily | None:
        """Direct lookup, mostly for tests + audit queries."""
        ...
