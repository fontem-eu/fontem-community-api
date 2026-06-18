"""Email-verification flow — issue + consume single-use links.

``issue(user)`` invalidates any outstanding verification token for the
account (so only the newest emailed link works), mints a fresh 24h
token, and sends the verification mail. Mail failure is swallowed
(logged) so registration never 500s on a flaky provider — the user
hits "resend".

``consume(token)`` redeems the link, stamps ``email_verified_at``, and
returns the user. Because the AuthorizationService rebuilds the
Principal from the DB every request, verification takes effect on the
caller's *next* request with no re-login.
"""
from __future__ import annotations

import hashlib
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from src.domain.user import User
from src.repositories.auth_token_repository import AuthToken, AuthTokenRepository
from src.repositories.user_repository import UserRepository
from src.services.email_templates import verification_email
from src.services.mail_service import MailMessage, MailService, MailSendError

logger = logging.getLogger("fontem.mail")

_PURPOSE = "verify_email"
_TTL = timedelta(hours=24)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _base_url() -> str:
    # The user-facing origin the verification link points at. Per-env
    # (staging vs prod) — injected via APP_BASE_URL. Trailing slash
    # stripped so we don't build `//verify-email`.
    return os.environ.get("APP_BASE_URL", "https://fontem.eu").rstrip("/")


class EmailVerificationService:
    def __init__(
        self,
        tokens: AuthTokenRepository,
        users: UserRepository,
        mail: MailService,
    ) -> None:
        self._tokens = tokens
        self._users = users
        self._mail = mail

    async def issue(self, user: User) -> None:
        """Mint + send a verification link. Idempotent-ish: any prior
        live token for this user is invalidated first."""
        await self._tokens.invalidate_outstanding(user.id, _PURPOSE)
        plaintext = secrets.token_urlsafe(32)
        await self._tokens.create(AuthToken(
            id=str(uuid4()),
            user_id=user.id,
            token_hash=_hash(plaintext),
            purpose=_PURPOSE,
            expires_at=_now() + _TTL,
        ))
        link = f"{_base_url()}/verify-email?token={plaintext}"
        subject, html, text = verification_email(user.name, link)
        try:
            await self._mail.send(MailMessage(
                to_email=user.email, to_name=user.name,
                subject=subject, html=html, text=text,
            ))
        except MailSendError as e:
            # Never fail the calling flow (register / resend) on a mail
            # error — the token is already persisted, the user can
            # retry. Log loudly for ops.
            logger.exception("verification mail to %s failed: %s", user.email, e)

    async def consume(self, plaintext: str) -> User | None:
        """Redeem a verification token. Returns the now-verified user,
        or None when the token is unknown / expired / already used."""
        token = await self._tokens.consume(_hash(plaintext), _PURPOSE, _now())
        if token is None:
            return None
        when = _now()
        await self._users.mark_email_verified(token.user_id, when)
        user = await self._users.get_by_id(token.user_id)
        return user
