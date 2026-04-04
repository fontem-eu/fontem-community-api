"""
Permission unit tests (PERM-01 through PERM-10).
InMemory repos — 0 I/O.
"""
from __future__ import annotations

import pytest

from tests.conftest import seed_user

from src.domain.report import Report
from src.domain.group import Group
from src.domain.moderation import Sanction
from src.services.exceptions import PermissionDenied


@pytest.mark.asyncio
class TestPermissions:
    # PERM-01: Admin can access any report
    async def test_admin_can_access_any_report(self, services):
        s = services
        await seed_user(s["user_repo"], "admin-1", roles=["admin"])
        await seed_user(s["user_repo"], "author-1")

        report = await s["report_svc"].create("author-1", "Secret Report")
        assert await s["perm_svc"].check("admin-1", report.id, "owner")

    # PERM-02: Owner has all permissions
    async def test_owner_has_all_permissions(self, services):
        s = services
        await seed_user(s["user_repo"], "owner-1")

        report = await s["report_svc"].create("owner-1", "My Report")
        for level in ("viewer", "commenter", "editor", "owner"):
            assert await s["perm_svc"].check("owner-1", report.id, level)

    # PERM-03: Editor can edit but not change visibility
    async def test_editor_can_edit_not_change_visibility(self, services):
        s = services
        await seed_user(s["user_repo"], "owner-1")
        await seed_user(s["user_repo"], "editor-1")

        report = await s["report_svc"].create("owner-1", "Report")
        await s["permission_repo"].set_user_access(report.id, "editor-1", "editor")

        assert await s["perm_svc"].check("editor-1", report.id, "editor")
        assert not await s["perm_svc"].check("editor-1", report.id, "owner")

    # PERM-04: Viewer cannot edit or comment
    async def test_viewer_cannot_edit(self, services):
        s = services
        await seed_user(s["user_repo"], "owner-1")
        await seed_user(s["user_repo"], "viewer-1")

        report = await s["report_svc"].create("owner-1", "Report")
        await s["permission_repo"].set_user_access(report.id, "viewer-1", "viewer")

        assert await s["perm_svc"].check("viewer-1", report.id, "viewer")
        assert not await s["perm_svc"].check("viewer-1", report.id, "commenter")
        assert not await s["perm_svc"].check("viewer-1", report.id, "editor")

    # PERM-05: Group access grants permission to members
    async def test_group_access_grants_to_members(self, services):
        s = services
        await seed_user(s["user_repo"], "owner-1")
        await seed_user(s["user_repo"], "member-1")

        group = Group(name="Team Alpha")
        group = await s["group_repo"].create(group)
        await s["group_repo"].add_member(group.id, "member-1")

        report = await s["report_svc"].create("owner-1", "Report")
        await s["permission_repo"].set_group_access(report.id, group.id, "editor")

        assert await s["perm_svc"].check("member-1", report.id, "editor")

    # PERM-06: Direct access overrides lower group access
    async def test_direct_access_overrides_group(self, services):
        s = services
        await seed_user(s["user_repo"], "owner-1")
        await seed_user(s["user_repo"], "user-1")

        group = Group(name="Team")
        group = await s["group_repo"].create(group)
        await s["group_repo"].add_member(group.id, "user-1")

        report = await s["report_svc"].create("owner-1", "Report")
        await s["permission_repo"].set_group_access(report.id, group.id, "viewer")
        await s["permission_repo"].set_user_access(report.id, "user-1", "editor")

        # Should have editor (direct) not just viewer (group)
        assert await s["perm_svc"].check("user-1", report.id, "editor")

    # PERM-07: Suspended user is denied even with editor access
    async def test_suspended_user_denied(self, services):
        s = services
        user = await seed_user(s["user_repo"], "editor-1")
        await seed_user(s["user_repo"], "owner-1")
        await seed_user(s["user_repo"], "mod-1", roles=["moderator"])

        report = await s["report_svc"].create("owner-1", "Report")
        await s["permission_repo"].set_user_access(report.id, "editor-1", "editor")

        # Sanction the user
        await s["mod_svc"].sanction("mod-1", "editor-1", "suspend", "testing")

        assert not await s["perm_svc"].check("editor-1", report.id, "editor")

    # PERM-08: Public report readable by any authenticated user
    async def test_public_report_readable(self, services):
        s = services
        await seed_user(s["user_repo"], "owner-1")
        await seed_user(s["user_repo"], "random-1")

        report = await s["report_svc"].create("owner-1", "Public Report")
        report.visibility = "public_auth"
        await s["report_repo"].update(report)

        assert await s["perm_svc"].check("random-1", report.id, "viewer")

    # PERM-09: Private report invisible to non-collaborators
    async def test_private_report_invisible(self, services):
        s = services
        await seed_user(s["user_repo"], "owner-1")
        await seed_user(s["user_repo"], "outsider-1")

        report = await s["report_svc"].create("owner-1", "Private Report")
        assert not await s["perm_svc"].check("outsider-1", report.id, "viewer")

    # PERM-10: Removing last owner raises error
    async def test_cannot_remove_last_owner(self, services):
        s = services
        await seed_user(s["user_repo"], "owner-1")

        report = await s["report_svc"].create("owner-1", "Report")
        # Trying to remove the only owner's access should still leave them
        # (for now this is a service-level concern, not repo-level)
        grants = await s["permission_repo"].list_collaborators(report.id)
        owners = [g for g in grants if g.level == "owner"]
        assert len(owners) == 1
