"""Email-verification + the 'Required' participation gate.

Covers:
- issue() mints a token + (suppress-mode) sends without raising.
- consume() marks the user verified and is single-use.
- the authz gate: an unverified user is denied participation actions
  but allowed reads; verifying flips it instantly (Principal is rebuilt
  from the DB each request).
"""
from __future__ import annotations

import pytest

from src.services.authz.actions import Action
from src.services.exceptions import PermissionDenied
from tests.conftest import seed_user


@pytest.mark.asyncio
class TestEmailVerificationService:
    async def test_issue_then_consume_verifies(self, services):
        s = services
        user = await seed_user(s["user_repo"], "newbie", email_verified=False)
        assert user.email_verified_at is None
        await s["email_verify_svc"].issue(user)
        # Grab the plaintext token from the in-memory repo (suppress
        # mode logged it; here we read it directly to redeem).
        token_rows = list(s["auth_token_repo"]._tokens.values())  # pylint: disable=protected-access
        assert len(token_rows) == 1
        # We stored only the hash; re-issue exposes plaintext via the
        # service. For the test we re-derive: issue() used token_urlsafe
        # then hashed — so we can't recover plaintext. Instead, test
        # consume() through a known token by issuing via a captured send.
        # Simplest: monkeypatch is overkill; assert the hash path via a
        # direct create+consume in the next test. Here just assert the
        # token exists and is unconsumed.
        assert token_rows[0].consumed_at is None
        assert token_rows[0].purpose == "verify_email"

    async def test_consume_unknown_token_returns_none(self, services):
        s = services
        result = await s["email_verify_svc"].consume("not-a-real-token")
        assert result is None

    async def test_full_roundtrip_via_captured_token(self, services, monkeypatch):
        """Capture the plaintext token by intercepting the mail send,
        then redeem it — the real end-to-end path."""
        s = services
        user = await seed_user(s["user_repo"], "roundtrip", email_verified=False)

        captured = {}
        original_send = s["mail_svc"].send

        async def _capture(msg):
            # The verification link carries ?token=<plaintext>.
            import re
            m = re.search(r"token=([A-Za-z0-9_-]+)", msg.text)
            if m:
                captured["token"] = m.group(1)
            return await original_send(msg)

        monkeypatch.setattr(s["mail_svc"], "send", _capture)
        await s["email_verify_svc"].issue(user)
        assert "token" in captured, "verification link should carry a token"

        verified = await s["email_verify_svc"].consume(captured["token"])
        assert verified is not None
        assert verified.email_verified_at is not None

        # Single-use: a second redeem fails.
        again = await s["email_verify_svc"].consume(captured["token"])
        assert again is None


@pytest.mark.asyncio
class TestVerificationGate:
    """The 'Required' gate: unverified accounts can read but not participate."""

    async def test_unverified_cannot_create_story(self, services):
        s = services
        user = await seed_user(s["user_repo"], "unv", email_verified=False)
        with pytest.raises(PermissionDenied, match="email not verified"):
            await s["report_svc"].create(user.id, "Blocked")

    async def test_unverified_can_still_read_public(self, services):
        s = services
        owner = await seed_user(s["user_repo"], "owner")
        r = await s["report_svc"].create(owner.id, "Public")
        await s["report_svc"].update(owner.id, r.id, visibility="public_open")
        unv = await seed_user(s["user_repo"], "unv", email_verified=False)
        # Read goes through STORIES_READ which is NOT gated.
        got = await s["report_svc"].get(unv.id, r.id)
        assert got.id == r.id

    async def test_verifying_unlocks_participation(self, services, monkeypatch):
        s = services
        user = await seed_user(s["user_repo"], "willverify", email_verified=False)
        # Before: blocked.
        with pytest.raises(PermissionDenied):
            await s["report_svc"].create(user.id, "Nope")
        # Verify via captured token.
        captured = {}
        orig = s["mail_svc"].send

        async def _cap(msg):
            import re
            m = re.search(r"token=([A-Za-z0-9_-]+)", msg.text)
            if m:
                captured["token"] = m.group(1)
            return await orig(msg)
        monkeypatch.setattr(s["mail_svc"], "send", _cap)
        await s["email_verify_svc"].issue(user)
        await s["email_verify_svc"].consume(captured["token"])
        # After: allowed. No re-login — the service reads email_verified
        # fresh from the repo on the next principal() build.
        created = await s["report_svc"].create(user.id, "Now allowed")
        assert created.title == "Now allowed"


@pytest.mark.asyncio
class TestPasswordReset:
    async def test_request_unknown_email_is_silent(self, services):
        s = services
        # No raise, no token created.
        await s["password_reset_svc"].request("ghost@nowhere.test")
        assert len(s["auth_token_repo"]._tokens) == 0  # pylint: disable=protected-access

    async def test_full_reset_roundtrip_revokes_sessions(self, services, monkeypatch):
        s = services
        import bcrypt
        from src.domain.user import User
        from tests.conftest import _stable_uuid
        uid = _stable_uuid("resetme")
        await s["user_repo"].upsert(User(
            id=uid, email="resetme@test.com", name="R",
            password_hash=bcrypt.hashpw(b"oldpassword", bcrypt.gensalt()).decode(),
        ))
        # Open a session for this user so we can prove reset kills it.
        issued = await s["refresh_token_svc"].issue_for_login(uid)

        captured = {}
        orig = s["mail_svc"].send

        async def _cap(msg):
            import re
            m = re.search(r"token=([A-Za-z0-9_-]+)", msg.text)
            if m:
                captured["token"] = m.group(1)
            return await orig(msg)
        monkeypatch.setattr(s["mail_svc"], "send", _cap)

        await s["password_reset_svc"].request("resetme@test.com")
        assert "token" in captured

        ok = await s["password_reset_svc"].reset(captured["token"], "brandnewpass")
        assert ok is True

        # The new password works (hash rotated).
        fresh = await s["user_repo"].get_by_id(uid)
        assert bcrypt.checkpw(b"brandnewpass", fresh.password_hash.encode())

        # The pre-reset session is dead (all families revoked).
        from src.services.refresh_token_service import InvalidRefreshToken
        with pytest.raises(InvalidRefreshToken):
            await s["refresh_token_svc"].rotate(issued.plaintext)

    async def test_reset_with_bad_token_fails(self, services):
        s = services
        ok = await s["password_reset_svc"].reset("garbage", "whatever12345")
        assert ok is False
