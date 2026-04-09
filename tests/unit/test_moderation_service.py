"""Additional moderation service tests for coverage."""
from __future__ import annotations

import pytest
from tests.conftest import seed_user, _stable_uuid
from src.services.exceptions import PermissionDenied, Conflict


@pytest.mark.asyncio
class TestModerationServiceExtra:
    """Cover _require_role, flag dedup, sanction types, lift, queue, log."""

    async def test_non_moderator_cannot_sanction(self, services):
        """Regular user cannot apply sanctions."""
        s = services
        await seed_user(s["user_repo"], "regular", trust_level="contributor")
        await seed_user(s["user_repo"], "target", trust_level="new_user")
        with pytest.raises(PermissionDenied):
            await s["mod_svc"].sanction(_stable_uuid("regular"), _stable_uuid("target"), "warning", "bad")

    async def test_moderator_can_warn(self, services):
        """Moderator can apply a warning."""
        s = services
        await seed_user(s["user_repo"], "mod-1", trust_level="moderator")
        await seed_user(s["user_repo"], "target", trust_level="new_user")
        result = await s["mod_svc"].sanction(_stable_uuid("mod-1"), _stable_uuid("target"), "warning", "spam")
        assert result.type == "warning"
        assert result.user_id == _stable_uuid("target")

    async def test_moderator_cannot_ban(self, services):
        """Moderator (non-admin) cannot ban."""
        s = services
        await seed_user(s["user_repo"], "mod-1", trust_level="moderator")
        await seed_user(s["user_repo"], "target", trust_level="new_user")
        with pytest.raises(PermissionDenied):
            await s["mod_svc"].sanction(_stable_uuid("mod-1"), _stable_uuid("target"), "ban", "bad")

    async def test_admin_can_ban(self, services):
        """Admin can ban."""
        s = services
        await seed_user(s["user_repo"], "admin-1", trust_level="admin", roles=["admin"])
        await seed_user(s["user_repo"], "target", trust_level="new_user")
        result = await s["mod_svc"].sanction(_stable_uuid("admin-1"), _stable_uuid("target"), "ban", "bad actor")
        assert result.type == "ban"

    async def test_duplicate_flag_raises_conflict(self, services):
        """Flagging the same content twice raises Conflict."""
        s = services
        await seed_user(s["user_repo"], "flagger", trust_level="contributor")
        await s["mod_svc"].flag(_stable_uuid("flagger"), "report", "r1", "spam")
        with pytest.raises(Conflict):
            await s["mod_svc"].flag(_stable_uuid("flagger"), "report", "r1", "spam")

    async def test_lift_sanction(self, services):
        """Lifting a sanction marks it as lifted."""
        s = services
        await seed_user(s["user_repo"], "mod-1", trust_level="moderator")
        await seed_user(s["user_repo"], "target", trust_level="new_user")
        result = await s["mod_svc"].sanction(_stable_uuid("mod-1"), _stable_uuid("target"), "mute", "noisy")
        await s["mod_svc"].lift(_stable_uuid("mod-1"), result.id)
        # Active sanction should now be None
        active = await s["user_repo"].get_active_sanction(_stable_uuid("target"))
        assert active is None

    async def test_get_queue_requires_moderator(self, services):
        """Non-moderator cannot access queue."""
        s = services
        await seed_user(s["user_repo"], "regular", trust_level="contributor")
        with pytest.raises(PermissionDenied):
            await s["mod_svc"].get_queue(_stable_uuid("regular"), 10, 0)

    async def test_get_log_requires_moderator(self, services):
        """Non-moderator cannot access log."""
        s = services
        await seed_user(s["user_repo"], "regular", trust_level="contributor")
        with pytest.raises(PermissionDenied):
            await s["mod_svc"].get_log(_stable_uuid("regular"), 10, 0)

    async def test_unknown_user_raises_permission_denied(self, services):
        """Sanction by non-existent user raises PermissionDenied."""
        s = services
        with pytest.raises(PermissionDenied):
            await s["mod_svc"].sanction(_stable_uuid("ghost"), _stable_uuid("target"), "warning", "bad")
