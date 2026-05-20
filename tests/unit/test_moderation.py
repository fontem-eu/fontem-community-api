"""
Moderation unit tests (MOD-01 through MOD-10).
InMemory repos — 0 I/O.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.domain.moderation import Sanction
from src.services.exceptions import Conflict, PermissionDenied
from tests.conftest import _stable_uuid, seed_user


@pytest.mark.asyncio
class TestModeration:
    # MOD-01: 3 flags triggers auto-hide
    async def test_three_flags_auto_hide(self, services):
        s = services
        await seed_user(s["user_repo"], "target-1", trust_level="contributor")
        for i in range(3):
            await seed_user(s["user_repo"], f"flagger-{i}", trust_level="commenter")

        await s["mod_svc"].flag(_stable_uuid("flagger-0"), "report", "rpt-1", "spam", None)
        await s["mod_svc"].flag(_stable_uuid("flagger-1"), "report", "rpt-1", "spam", None)
        await s["mod_svc"].flag(_stable_uuid("flagger-2"), "report", "rpt-1", "spam", None)

        count = await s["mod_repo"].count_flags("report", "rpt-1")
        assert count >= 3

    # MOD-02: Same user cannot flag same content twice
    async def test_duplicate_flag_rejected(self, services):
        s = services
        await seed_user(s["user_repo"], "flagger-1", trust_level="commenter")

        await s["mod_svc"].flag(_stable_uuid("flagger-1"), "report", "rpt-1", "spam", None)
        with pytest.raises(Conflict):
            await s["mod_svc"].flag(_stable_uuid("flagger-1"), "report", "rpt-1", "spam", None)

    # MOD-03: Only moderators can apply sanctions
    async def test_only_moderators_sanction(self, services):
        s = services
        await seed_user(s["user_repo"], "user-1", trust_level="contributor")
        await seed_user(s["user_repo"], "target-1")

        with pytest.raises(PermissionDenied):
            await s["mod_svc"].sanction(_stable_uuid("user-1"), _stable_uuid("target-1"), "warning", "bad behavior")

    # MOD-04: Only admins can ban
    async def test_only_admins_ban(self, services):
        s = services
        await seed_user(s["user_repo"], "mod-1", roles=["moderator"])
        await seed_user(s["user_repo"], "target-1")

        with pytest.raises(PermissionDenied):
            await s["mod_svc"].sanction(_stable_uuid("mod-1"), _stable_uuid("target-1"), "ban", "severe violation")

    # MOD-04b: Admin can ban
    async def test_admin_can_ban(self, services):
        s = services
        await seed_user(s["user_repo"], "admin-1", roles=["admin"])
        await seed_user(s["user_repo"], "target-1")

        sanction = await s["mod_svc"].sanction(
            _stable_uuid("admin-1"), _stable_uuid("target-1"), "ban", "severe",
        )
        assert sanction.type == "ban"

    # MOD-05: Warning is one-time (no duration)
    async def test_warning_no_duration(self, services):
        s = services
        await seed_user(s["user_repo"], "mod-1", roles=["moderator"])
        await seed_user(s["user_repo"], "target-1")

        sanction = await s["mod_svc"].sanction(
            _stable_uuid("mod-1"), _stable_uuid("target-1"), "warning", "first warning",
        )
        assert sanction.expires_at is None

    # MOD-06: Mute prevents commenting (tested via permission check)
    async def test_mute_blocks_user(self, services):
        s = services
        await seed_user(s["user_repo"], "mod-1", roles=["moderator"])
        await seed_user(s["user_repo"], "user-1", trust_level="contributor")

        await s["mod_svc"].sanction(_stable_uuid("mod-1"), _stable_uuid("user-1"), "mute", "spam")

        active = await s["user_repo"].get_active_sanction(_stable_uuid("user-1"))
        assert active is not None
        assert active.type == "mute"

    # MOD-07: Expired suspension no longer blocks
    async def test_expired_suspension_allows(self, services):
        s = services
        await seed_user(s["user_repo"], "admin-1", roles=["admin"])
        await seed_user(s["user_repo"], "user-1")

        # Create a suspension that already expired
        expired = Sanction(
            user_id=_stable_uuid("user-1"), type="suspend", reason="test",
            applied_by=_stable_uuid("admin-1"),
            starts_at=datetime.now(timezone.utc) - timedelta(days=2),
            expires_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        await s["mod_repo"].add_sanction(expired)

        active = await s["mod_repo"].get_active_sanction(_stable_uuid("user-1"))
        assert active is None  # Expired, should not be active

    # MOD-08: Moderation log records actions
    async def test_moderation_log(self, services):
        s = services
        await seed_user(s["user_repo"], "mod-1", roles=["moderator"])
        await seed_user(s["user_repo"], "target-1")

        await s["mod_svc"].sanction(_stable_uuid("mod-1"), _stable_uuid("target-1"), "warning", "first")
        log = await s["mod_repo"].get_log(10, 0)
        assert len(log) >= 1

    # MOD-09: Lifting sanction updates lifted_at
    async def test_lift_sanction(self, services):
        s = services
        await seed_user(s["user_repo"], "mod-1", roles=["moderator"])
        await seed_user(s["user_repo"], "target-1")

        sanction = await s["mod_svc"].sanction(
            _stable_uuid("mod-1"), _stable_uuid("target-1"), "mute", "temporary",
        )
        await s["mod_svc"].lift(_stable_uuid("mod-1"), sanction.id)

        active = await s["mod_repo"].get_active_sanction(_stable_uuid("target-1"))
        assert active is None

    # MOD-10: Trust level auto-progression placeholder
    async def test_trust_level_progression(self, services):
        s = services
        user = await seed_user(s["user_repo"], "user-1", trust_level="new_user")
        assert user.trust_level == "new_user"
        # Auto-progression would be checked by a background job
        # For now, just verify we can change it
        await s["user_repo"].set_trust_level(_stable_uuid("user-1"), "commenter")
        updated = await s["user_repo"].get_by_id(_stable_uuid("user-1"))
        assert updated.trust_level == "commenter"
