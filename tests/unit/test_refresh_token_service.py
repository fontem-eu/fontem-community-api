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
# pylint: disable=redefined-outer-name
#   pytest fixtures shadow their own names by design.
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from src.infra.memory.mem_refresh_token_repo import InMemoryRefreshTokenRepository
from src.services.refresh_token_service import (
    REFRESH_GRACE,
    InvalidRefreshToken,
    RefreshTokenService,
)


@pytest.fixture
def repo():
    return InMemoryRefreshTokenRepository()


@pytest.fixture
def svc(repo):
    # Shares the repo, so a test can age a family's rotated_at to look at
    # what happens once the grace window has closed.
    return RefreshTokenService(repo)


@pytest.mark.asyncio
class TestRefreshTokenService:
    async def test_issue_then_rotate_succeeds(self, svc):
        issued = await svc.issue_for_login("user-1")
        rotated = await svc.rotate(issued.plaintext)
        assert rotated.plaintext != issued.plaintext
        assert rotated.family.id == issued.family.id  # same family

    async def test_the_old_token_still_works_briefly_after_rotation(self, svc):
        """A second tab is not a thief.

        Rotation is single-use, but "single-use" and "the only request in
        flight" are different claims. A browser has one cookie jar and
        several tabs; when one rotates, another may already have a request
        out carrying the token that was current a moment ago. Rejecting it
        logged the user out of both — 14 × 401 in a single e2e run.

        Inside REFRESH_GRACE the old token is accepted WITHOUT rotating
        again: the winner's Set-Cookie already carries the current token
        for this browser, so there is nothing to hand back.
        """
        issued = await svc.issue_for_login("user-1")
        await svc.rotate(issued.plaintext)
        again = await svc.rotate(issued.plaintext)
        assert again.family.id == issued.family.id
        assert again.plaintext is None, (
            "a grace-window refresh must not start a third generation")

    async def test_the_old_token_is_theft_once_the_window_closes(self, svc, repo):
        """Outside the window the same hit is the reuse signal this module
        has always documented — and now acts on. The family is revoked, so
        neither the thief nor the victim can trade it for a session."""
        issued = await svc.issue_for_login("user-1")
        rotated = await svc.rotate(issued.plaintext)
        # Age the rotation past the grace window.
        aged = replace(
            await repo.get_by_id(rotated.family.id),
            rotated_at=datetime.now(timezone.utc) - REFRESH_GRACE - timedelta(seconds=1),
        )
        repo._families[rotated.family.id] = aged  # pylint: disable=protected-access

        with pytest.raises(InvalidRefreshToken):
            await svc.rotate(issued.plaintext)

        family = await repo.get_by_id(rotated.family.id)
        assert family.revoked_at is not None
        assert "reuse" in (family.revoked_reason or "")

    async def test_the_current_token_is_dead_too_once_reuse_is_detected(self, svc, repo):
        """The point of revoking the family: a thief who replays an old
        token must not leave the victim with a working session either."""
        issued = await svc.issue_for_login("user-1")
        rotated = await svc.rotate(issued.plaintext)
        aged = replace(
            await repo.get_by_id(rotated.family.id),
            rotated_at=datetime.now(timezone.utc) - REFRESH_GRACE - timedelta(seconds=1),
        )
        repo._families[rotated.family.id] = aged  # pylint: disable=protected-access
        with pytest.raises(InvalidRefreshToken):
            await svc.rotate(issued.plaintext)
        with pytest.raises(InvalidRefreshToken):
            await svc.rotate(rotated.plaintext)

    async def test_a_token_two_generations_back_is_never_graced(self, svc):
        """Only the immediately-previous token is a plausible race. Older
        ones match nothing and stay unknown."""
        issued = await svc.issue_for_login("user-1")
        second = await svc.rotate(issued.plaintext)
        await svc.rotate(second.plaintext)
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
