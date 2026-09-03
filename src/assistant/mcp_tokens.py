"""Personal access tokens for connecting an external client to Dargle.

These are what a user pastes into Claude Desktop, Claude Code or ChatGPT
so their own client can reach our MCP tools. They authorise access to
*Dargle data* — which is entirely ours to grant — and are a different
thing from the LLM provider credential we deliberately do not hold.

Only the SHA-256 hash is stored, following the same discipline as
auth_tokens: a database dump cannot be replayed into somebody's account.
The plaintext is shown exactly once, at creation, and cannot be recovered
afterwards. That is a deliberate inconvenience — a token you can re-read
is a token an attacker can re-read.

Unlike auth_tokens these are multi-use and long-lived: a client presents
the same token on every call, for months. So they carry a label (which
client is this?) and last_used_at, because the only way to safely revoke
one is to know which is which.
"""
from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime

#: Prefix makes a leaked token greppable in logs and recognisable to the
#: user as ours rather than a provider key.
TOKEN_PREFIX = "fontem_mcp_"


def generate_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def looks_like_mcp_token(token: str) -> bool:
    """Cheap shape check before touching the database."""
    return bool(token) and token.startswith(TOKEN_PREFIX)


@dataclass(frozen=True)
class McpTokenSummary:
    """What a caller may see: never the token itself."""
    id: str
    label: str
    created_at: datetime
    last_used_at: datetime | None

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "created_at": self.created_at.isoformat(),
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
        }
