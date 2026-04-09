"""Extended unit tests for ModerationService — cover uncovered paths."""
from __future__ import annotations

import pytest
from tests.conftest import seed_user, _stable_uuid
from src.services.exceptions import PermissionDenied


@pytest.mark.asyncio
class TestModerationServiceExtended:
    """Additional ModerationService coverage."""

    async def test_lift_sanction(self, services):
        """Lifting a sanction marks it and syncs to user repo."""
        s = services
        await seed_user(s["user_repo"], "mod-1", trust_level="moderator", roles=["moderator"])
        await seed_user(s["user_repo"], "user-1")

        sanction = await s["mod_svc"].sanction(_stable_uuid("mod-1"), _stable_uuid("user-1"), "mute", "test")
        await s["mod_svc"].lift(_stable_uuid("mod-1"), sanction.id)

        # User should no longer have active sanction
        active = await s["user_repo"].get_active_sanction(_stable_uuid("user-1"))
        assert active is None

    async def test_sanction_requires_moderator(self, services):
        """Non-moderator cannot apply sanctions."""
        s = services
        await seed_user(s["user_repo"], "user-1")
        await seed_user(s["user_repo"], "target-1")
        with pytest.raises(PermissionDenied):
            await s["mod_svc"].sanction(_stable_uuid("user-1"), _stable_uuid("target-1"), "warning", "nope")

    async def test_ban_requires_admin(self, services):
        """Ban requires admin, not just moderator."""
        s = services
        await seed_user(s["user_repo"], "mod-1", trust_level="moderator", roles=["moderator"])
        await seed_user(s["user_repo"], "user-1")
        with pytest.raises(PermissionDenied):
            await s["mod_svc"].sanction(_stable_uuid("mod-1"), _stable_uuid("user-1"), "ban", "nope")

    async def test_admin_can_ban(self, services):
        """Admin can apply ban."""
        s = services
        await seed_user(s["user_repo"], "admin-1", trust_level="admin", roles=["admin"])
        await seed_user(s["user_repo"], "user-1")
        result = await s["mod_svc"].sanction(_stable_uuid("admin-1"), _stable_uuid("user-1"), "ban", "bad actor")
        assert result.type == "ban"

    async def test_get_log(self, services):
        """get_log returns moderation log entries."""
        s = services
        await seed_user(s["user_repo"], "mod-1", trust_level="moderator", roles=["moderator"])
        log = await s["mod_svc"].get_log(_stable_uuid("mod-1"), 10, 0)
        assert isinstance(log, list)

    async def test_get_queue(self, services):
        """get_queue returns flagged content queue."""
        s = services
        await seed_user(s["user_repo"], "mod-1", trust_level="moderator", roles=["moderator"])
        queue = await s["mod_svc"].get_queue(_stable_uuid("mod-1"), 10, 0)
        assert isinstance(queue, list)
