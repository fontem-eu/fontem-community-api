"""Password-reset flow — request + redeem single-use links.

``request(email)`` is deliberately enumeration-safe: it ALWAYS returns
without signalling whether the email matched an account. When it does
match, a 1h single-use token is minted and emailed; when it doesn't,
nothing happens but the caller can't tell the difference (same code
path, same timing characteristics modulo the bcrypt-less branch —
acceptable here because reset isn't a credential check).

``reset(token, new_password)`` redeems the token, rotates the bcrypt
hash, and — critically — revokes every refresh-token family for the
account. A password reset is the recover-from-compromise path; it
must kill any session an attacker established.
"""
from __future__ import annotations

import hashlib
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import bcrypt

from src.repositories.auth_token_repository import AuthToken, AuthTokenRepository
from src.repositories.user_repository import UserRepository
from src.services.email_templates import password_reset_email
from src.services.mail_service import MailMessage, MailService, MailSendError
from src.services.refresh_token_service import RefreshTokenService

logger = logging.getLogger("fontem.mail")

_PURPOSE = "password_reset"
_TTL = timedelta(hours=1)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _base_url() -> str:
    return os.environ.get("APP_BASE_URL", "https://fontem.eu").rstrip("/")


class PasswordResetService:
    def __init__(
        self,
        tokens: AuthTokenRepository,
        users: UserRepository,
        mail: MailService,
        refresh: RefreshTokenService,
    ) -> None:
        self._tokens = tokens
        self._users = users
        self._mail = mail
        self._refresh = refresh

    async def request(self, email: str) -> None:
        """Always silent. Mints + sends a reset link only if the email
        matches a local (password-bearing) account."""
        user = await self._users.get_by_email(email)
        # OAuth-only accounts have no password to reset; treat the same
        # as no-account so we don't hint at the account's existence OR
        # its auth method.
        if user is None or user.password_hash is None:
            return
        await self._tokens.invalidate_outstanding(user.id, _PURPOSE)
        plaintext = secrets.token_urlsafe(32)
        await self._tokens.create(AuthToken(
            id=str(uuid4()),
            user_id=user.id,
            token_hash=_hash(plaintext),
            purpose=_PURPOSE,
            expires_at=_now() + _TTL,
        ))
        link = f"{_base_url()}/reset-password?token={plaintext}"
        subject, html, text = password_reset_email(user.name, link)
        try:
            await self._mail.send(MailMessage(
                to_email=user.email, to_name=user.name,
                subject=subject, html=html, text=text,
            ))
        except MailSendError as e:
            logger.exception("password-reset mail to %s failed: %s", user.email, e)

    async def reset(self, plaintext: str, new_password: str) -> bool:
        """Redeem the token + rotate the password. Returns False when
        the token is unknown / expired / already used.

        On success every refresh-token family for the user is revoked
        — a reset is the account-recovery path, so any session an
        attacker holds dies here.
        """
        token = await self._tokens.consume(_hash(plaintext), _PURPOSE, _now())
        if token is None:
            return False
        new_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
        await self._users.update_password(token.user_id, new_hash)
        await self._refresh.revoke_all_for_user(token.user_id)
        return True
