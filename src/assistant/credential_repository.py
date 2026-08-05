"""Storage for per-user provider credentials.

Deliberately narrow. `get_secret_for_turn` is the only way plaintext ever
leaves this module, and it is named so that a reviewer notices a second
caller appearing.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.assistant.credentials import (
    CredentialSummary,
    decrypt,
    encrypt,
    fingerprint,
)
from src.assistant.mcp_tokens import (
    McpTokenSummary,
    generate_token,
    hash_token,
    looks_like_mcp_token,
)
from src.infra.postgres.models import McpTokenModel, UserLLMCredentialModel


class CredentialRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(
        self, user_id: str, provider: str, secret: str, model: str | None,
    ) -> CredentialSummary:
        """Store (or replace) the user's key for one provider."""
        row = await self._row(user_id, provider)
        enc, fp = encrypt(secret), fingerprint(secret)
        if row is None:
            row = UserLLMCredentialModel(
                user_id=user_id, provider=provider, secret_enc=enc,
                fingerprint=fp, model=model,
            )
            self._session.add(row)
        else:
            row.secret_enc = enc
            row.fingerprint = fp
            row.model = model
            # A replaced key has not been used yet; keeping the old
            # timestamp would claim it had.
            row.last_used_at = None
        await self._session.flush()
        return _summary(row)

    async def list_for_user(self, user_id: str) -> list[CredentialSummary]:
        rows = (await self._session.execute(
            select(UserLLMCredentialModel)
            .where(UserLLMCredentialModel.user_id == user_id)
            .order_by(UserLLMCredentialModel.provider)
        )).scalars().all()
        return [_summary(r) for r in rows]

    async def delete(self, user_id: str, provider: str) -> bool:
        result = await self._session.execute(
            delete(UserLLMCredentialModel).where(
                UserLLMCredentialModel.user_id == user_id,
                UserLLMCredentialModel.provider == provider,
            )
        )
        return bool(result.rowcount)

    async def get_secret_for_turn(
        self, user_id: str, provider: str | None = None,
    ) -> tuple[str, str, str | None] | None:
        """Plaintext key for spending on this user's own turn.

        Returns (provider, secret, model) or None. The ONLY place plaintext
        leaves storage. Also stamps last_used_at, so a user can see whether
        a key they added is actually being used.
        """
        row = await self._row(user_id, provider) if provider else await self._any_row(user_id)
        if row is None:
            return None
        secret = decrypt(row.secret_enc)
        await self._session.execute(
            update(UserLLMCredentialModel)
            .where(UserLLMCredentialModel.id == row.id)
            .values(last_used_at=datetime.now(timezone.utc))
        )
        return row.provider, secret, row.model

    async def _row(self, user_id: str, provider: str) -> UserLLMCredentialModel | None:
        return (await self._session.execute(
            select(UserLLMCredentialModel).where(
                UserLLMCredentialModel.user_id == user_id,
                UserLLMCredentialModel.provider == provider,
            )
        )).scalars().first()

    async def _any_row(self, user_id: str) -> UserLLMCredentialModel | None:
        """Whichever key the user has, most recently used first."""
        return (await self._session.execute(
            select(UserLLMCredentialModel)
            .where(UserLLMCredentialModel.user_id == user_id)
            .order_by(UserLLMCredentialModel.last_used_at.desc().nullslast(),
                      UserLLMCredentialModel.created_at.desc())
        )).scalars().first()


def _summary(row: UserLLMCredentialModel) -> CredentialSummary:
    return CredentialSummary(
        provider=row.provider, model=row.model, fingerprint=row.fingerprint,
        created_at=row.created_at, last_used_at=row.last_used_at,
    )


class McpTokenRepository:
    """Personal access tokens for external MCP clients.

    `verify` is the hot path — every tool call from a connected client
    presents a token — so it is a single indexed lookup on the hash.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, user_id: str, label: str) -> tuple[str, McpTokenSummary]:
        """Mint a token. Returns (plaintext, summary).

        The plaintext is returned exactly once and never stored. The
        caller must show it now or lose it — a token that can be re-read
        later can be re-read by whoever gets in later.
        """
        plaintext = generate_token()
        row = McpTokenModel(
            user_id=user_id, token_hash=hash_token(plaintext),
            label=(label or "").strip()[:80] or "Unnamed client",
        )
        self._session.add(row)
        await self._session.flush()
        return plaintext, _mcp_summary(row)

    async def list_for_user(self, user_id: str) -> list[McpTokenSummary]:
        rows = (await self._session.execute(
            select(McpTokenModel)
            .where(McpTokenModel.user_id == user_id)
            .order_by(McpTokenModel.created_at.desc())
        )).scalars().all()
        return [_mcp_summary(r) for r in rows]

    async def revoke(self, user_id: str, token_id: str) -> bool:
        result = await self._session.execute(
            delete(McpTokenModel).where(
                McpTokenModel.user_id == user_id, McpTokenModel.id == token_id,
            )
        )
        return bool(result.rowcount)

    async def verify(self, plaintext: str) -> str | None:
        """Resolve a presented token to a user id, or None.

        Shape-checked before the query so a stray Authorization header
        does not become a database round-trip on every request.
        """
        if not looks_like_mcp_token(plaintext):
            return None
        row = (await self._session.execute(
            select(McpTokenModel).where(
                McpTokenModel.token_hash == hash_token(plaintext)
            )
        )).scalars().first()
        if row is None:
            return None
        await self._session.execute(
            update(McpTokenModel).where(McpTokenModel.id == row.id)
            .values(last_used_at=datetime.now(timezone.utc))
        )
        return row.user_id


def _mcp_summary(row: McpTokenModel) -> McpTokenSummary:
    return McpTokenSummary(
        id=row.id, label=row.label,
        created_at=row.created_at, last_used_at=row.last_used_at,
    )
