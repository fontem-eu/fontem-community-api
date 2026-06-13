"""Service-layer tests for RefreshTokenService.

The four properties we pin (the floor described in the design doc):

1. **rotation** — every successful rotate swaps the family's stored
   hash so the offered token no longer validates.
2. **reuse detection** — replaying an already-rotated token after the
   family has moved on must 401.
3. **logout revokes the session** — the cookie is dead after.
4. **sign-out-everywhere revokes every active family for the user**
   without touching a different user's families.
"""
from __future__ import annotations

import pytest

from src.infra.memory.mem_refresh_token_repo import InMemoryRefreshTokenRepository
from src.services.refresh_token_service import (
    InvalidRefreshToken,
    RefreshTokenService,
)


@pytest.fixture
def svc():
    return RefreshTokenService(InMemoryRefreshTokenRepository())


@pytest.mark.asyncio
class TestRefreshTokenService:
    async def test_issue_then_rotate_succeeds(self, svc):
        issued = await svc.issue_for_login("user-1")
        rotated = await svc.rotate(issued.plaintext)
        assert rotated.plaintext != issued.plaintext
        assert rotated.family.id == issued.family.id  # same family

    async def test_rotate_invalidates_the_old_token(self, svc):
        """The novel property: after rotation, the *old* plaintext
        must not validate. This is what makes refresh-token-reuse
        fail loud."""
        issued = await svc.issue_for_login("user-1")
        await svc.rotate(issued.plaintext)
        # Now an attacker (or stale tab) presents the original token:
        with pytest.raises(InvalidRefreshToken):
            await svc.rotate(issued.plaintext)

    async def test_unknown_token_raises(self, svc):
        with pytest.raises(InvalidRefreshToken):
            await svc.rotate("definitely-not-a-real-token-AAAA")

    async def test_logout_revokes_session(self, svc):
        issued = await svc.issue_for_login("user-1")
        await svc.revoke(issued.plaintext)
        with pytest.raises(InvalidRefreshToken):
            await svc.rotate(issued.plaintext)

    async def test_logout_is_idempotent(self, svc):
        issued = await svc.issue_for_login("user-1")
        await svc.revoke(issued.plaintext)
        # Re-revoking is fine; we just want the second call not to raise.
        await svc.revoke(issued.plaintext)

    async def test_logout_doesnt_affect_other_sessions(self, svc):
        a = await svc.issue_for_login("user-1")
        b = await svc.issue_for_login("user-1")  # second device
        await svc.revoke(a.plaintext)
        # b is still alive.
        rotated_b = await svc.rotate(b.plaintext)
        assert rotated_b.family.id == b.family.id

    async def test_sign_out_everywhere_kills_all_user_families(self, svc):
        a = await svc.issue_for_login("user-1")
        b = await svc.issue_for_login("user-1")
        other = await svc.issue_for_login("user-2")
        revoked = await svc.revoke_all_for_user("user-1")
        assert revoked == 2
        for plaintext in (a.plaintext, b.plaintext):
            with pytest.raises(InvalidRefreshToken):
                await svc.rotate(plaintext)
        # The unrelated user's session survives.
        rotated_other = await svc.rotate(other.plaintext)
        assert rotated_other.family.user_id == "user-2"

    async def test_sign_out_everywhere_idempotent(self, svc):
        await svc.issue_for_login("user-1")
        first = await svc.revoke_all_for_user("user-1")
        second = await svc.revoke_all_for_user("user-1")
        assert first == 1
        assert second == 0  # already revoked; nothing more to do
