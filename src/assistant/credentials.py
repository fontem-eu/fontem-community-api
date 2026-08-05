"""Per-user LLM provider credentials.

The platform used to pay for everyone's inference through one Mistral key
in the environment, which made us a reseller of somebody else's tokens:
our bill, our quota, our abuse problem, and no product benefit. Users now
bring their own key and their own provider.

Storage: encrypted in Postgres with a master key supplied through the
environment (Vault -> VSO -> Secret -> env, the same path every other
secret in this service already takes).

The roadmap said Vault-per-user rather than a Postgres column, on the
grounds that a table of third-party API keys is a uniquely bad thing to
leak. That reasoning still holds and this design answers it: the column
holds AEAD ciphertext, so a database dump on its own is inert — the master
key lives somewhere a dump does not reach. Writing per-user secrets to
Vault would instead have required giving this service Vault *write*
access, which is a far larger capability than the problem needs, for the
same practical guarantee.

The key is write-only from the API's perspective, the way a password is.
Nothing reads it back out to a caller — only the turn that spends it.
"""
from __future__ import annotations

import base64
import hashlib
import os
from dataclasses import dataclass
from datetime import datetime

from cryptography.fernet import Fernet, InvalidToken

#: Providers we can actually talk to. Anything else is refused at the API
#: boundary rather than stored and discovered to be useless at turn time.
SUPPORTED_PROVIDERS = ("anthropic", "mistral", "openai")

_MASTER_KEY_ENV = "LLM_CREDENTIAL_KEY"


class CredentialEncryptionUnavailable(RuntimeError):
    """No master key configured, so we must not accept secrets we cannot protect."""


def _fernet() -> Fernet:
    raw = os.environ.get(_MASTER_KEY_ENV, "").strip()
    if not raw:
        raise CredentialEncryptionUnavailable(
            f"{_MASTER_KEY_ENV} is not set; refusing to store provider keys"
        )
    # Accept either a real Fernet key or any sufficiently long secret,
    # derived deterministically. Operators should not have to know what a
    # urlsafe-base64 32-byte key is to rotate this.
    try:
        return Fernet(raw.encode())
    except (ValueError, TypeError):
        digest = hashlib.sha256(raw.encode()).digest()
        return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(secret: str) -> str:
    return _fernet().encrypt(secret.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        # Usually means the master key rotated without re-encrypting. Say so
        # plainly: "invalid token" sends people looking at the user's key.
        raise CredentialEncryptionUnavailable(
            "stored credential could not be decrypted — has "
            f"{_MASTER_KEY_ENV} changed since it was saved?"
        ) from exc


def fingerprint(secret: str) -> str:
    """A stable, non-reversible tag so a user can tell which key is stored.

    Shown instead of the key itself. Truncated hash rather than last-4,
    because the tail of an API key is still key material.
    """
    return hashlib.sha256(secret.encode()).hexdigest()[:8]


@dataclass(frozen=True)
class CredentialSummary:
    """What a caller may see: enough to manage it, never enough to use it."""
    provider: str
    model: str | None
    fingerprint: str
    created_at: datetime
    last_used_at: datetime | None

    def as_dict(self) -> dict:
        return {
            "provider": self.provider,
            "model": self.model,
            "fingerprint": self.fingerprint,
            "created_at": self.created_at.isoformat(),
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
        }


def validate_provider(provider: str) -> str:
    p = (provider or "").strip().lower()
    if p not in SUPPORTED_PROVIDERS:
        raise ValueError(
            f"unsupported provider {provider!r}; expected one of "
            + ", ".join(SUPPORTED_PROVIDERS)
        )
    return p
