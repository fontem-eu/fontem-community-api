from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from src.domain.moderation import Sanction
from src.domain.user import User


class UserRepository(ABC):
    @abstractmethod
    async def get_by_id(self, user_id: str) -> User | None: ...

    @abstractmethod
    async def get_by_email(self, email: str) -> User | None: ...

    @abstractmethod
    async def upsert(self, user: User) -> User: ...

    @abstractmethod
    async def get_roles(self, user_id: str) -> list[str]: ...

    @abstractmethod
    async def set_roles(self, user_id: str, roles: list[str]) -> None: ...

    @abstractmethod
    async def get_active_sanction(self, user_id: str) -> Sanction | None: ...

    @abstractmethod
    async def add_sanction(self, sanction: Sanction) -> None: ...

    @abstractmethod
    async def lift_sanction(self, sanction_id: str) -> None: ...

    @abstractmethod
    async def register_failed_login(
        self, email: str, max_attempts: int, lock_duration_minutes: int,
    ) -> None:
        """Increment failed-login counter for the given email.

        If the resulting count reaches ``max_attempts``, set ``locked_until``
        to ``now + lock_duration_minutes``. Silent no-op if the email is
        unknown (do not leak account existence).
        """

    @abstractmethod
    async def mark_email_verified(self, user_id: str, when) -> None:
        """Set ``email_verified_at``. Idempotent — re-verifying is a no-op
        at the policy layer (the account is already verified)."""
        ...

    @abstractmethod
    async def update_password(self, user_id: str, password_hash: str) -> None:
        """Replace the bcrypt hash. Used by the password-reset flow."""
        ...

    @abstractmethod
    async def clear_failed_logins(self, user_id: str) -> None:
        """Reset failed-login counter and clear any lock for the given user."""

    @abstractmethod
    async def record_login(self, user_id: str, when: datetime) -> None:
        """Stamp ``last_login_at``.

        Called once per successful sign-in, from the single seam every login
        path shares. A token refresh is not a sign-in and must not call it —
        that is what a session's rotation time already records.
        """
