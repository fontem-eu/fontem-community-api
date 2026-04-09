"""Tests for ReportService — verify permission enforcement and error behavior
through the public service API.

No mocking of internals — tests exercise the full service stack with
InMemory repositories.
"""
from __future__ import annotations

import pytest
from tests.conftest import seed_user
from src.services.exceptions import Conflict, NotFound, PermissionDenied


@pytest.mark.asyncio
class TestReportPermissions:
    """Verify that each operation enforces the correct access level."""

    async def test_viewer_can_read_report(self, services):
        s = services
        owner = await seed_user(s["user_repo"], "owner")
        viewer = await seed_user(s["user_repo"], "viewer")
        r = await s["report_svc"].create(owner.id, "Report")
        await s["perm_svc"].grant_access(r.id, viewer.id, "viewer")
        report = await s["report_svc"].get(viewer.id, r.id)
        assert report.title == "Report"

    async def test_viewer_cannot_update_report(self, services):
        s = services
        owner = await seed_user(s["user_repo"], "owner")
        viewer = await seed_user(s["user_repo"], "viewer")
        r = await s["report_svc"].create(owner.id, "Report")
        await s["perm_svc"].grant_access(r.id, viewer.id, "viewer")
        with pytest.raises(PermissionDenied):
            await s["report_svc"].update(viewer.id, r.id, title="Hacked")

    async def test_viewer_cannot_delete_report(self, services):
        s = services
        owner = await seed_user(s["user_repo"], "owner")
        viewer = await seed_user(s["user_repo"], "viewer")
        r = await s["report_svc"].create(owner.id, "Report")
        await s["perm_svc"].grant_access(r.id, viewer.id, "viewer")
        with pytest.raises(PermissionDenied):
            await s["report_svc"].delete(viewer.id, r.id)

    async def test_editor_can_add_section(self, services):
        s = services
        owner = await seed_user(s["user_repo"], "owner")
        editor = await seed_user(s["user_repo"], "editor")
        r = await s["report_svc"].create(owner.id, "Report")
        await s["perm_svc"].grant_access(r.id, editor.id, "editor")
        sec = await s["report_svc"].add_section(editor.id, r.id, {"text": "hi"})
        assert sec.content_json == {"text": "hi"}

    async def test_viewer_cannot_add_section(self, services):
        s = services
        owner = await seed_user(s["user_repo"], "owner")
        viewer = await seed_user(s["user_repo"], "viewer")
        r = await s["report_svc"].create(owner.id, "Report")
        await s["perm_svc"].grant_access(r.id, viewer.id, "viewer")
        with pytest.raises(PermissionDenied):
            await s["report_svc"].add_section(viewer.id, r.id, {"text": "hi"})

    async def test_editor_can_edit_section(self, services):
        s = services
        owner = await seed_user(s["user_repo"], "owner")
        editor = await seed_user(s["user_repo"], "editor")
        r = await s["report_svc"].create(owner.id, "Report")
        await s["perm_svc"].grant_access(r.id, editor.id, "editor")
        sec = await s["report_svc"].add_section(owner.id, r.id, {"v": 1})
        updated = await s["report_svc"].edit_section(editor.id, sec.id, {"v": 2})
        assert updated.content_json == {"v": 2}

    async def test_viewer_cannot_edit_section(self, services):
        s = services
        owner = await seed_user(s["user_repo"], "owner")
        viewer = await seed_user(s["user_repo"], "viewer")
        r = await s["report_svc"].create(owner.id, "Report")
        await s["perm_svc"].grant_access(r.id, viewer.id, "viewer")
        sec = await s["report_svc"].add_section(owner.id, r.id, {"v": 1})
        with pytest.raises(PermissionDenied):
            await s["report_svc"].edit_section(viewer.id, sec.id, {"v": 2})

    async def test_viewer_cannot_delete_section(self, services):
        s = services
        owner = await seed_user(s["user_repo"], "owner")
        viewer = await seed_user(s["user_repo"], "viewer")
        r = await s["report_svc"].create(owner.id, "Report")
        await s["perm_svc"].grant_access(r.id, viewer.id, "viewer")
        sec = await s["report_svc"].add_section(owner.id, r.id, {"v": 1})
        with pytest.raises(PermissionDenied):
            await s["report_svc"].delete_section(viewer.id, sec.id)

    async def test_viewer_cannot_acquire_lock(self, services):
        s = services
        owner = await seed_user(s["user_repo"], "owner")
        viewer = await seed_user(s["user_repo"], "viewer")
        r = await s["report_svc"].create(owner.id, "Report")
        await s["perm_svc"].grant_access(r.id, viewer.id, "viewer")
        sec = await s["report_svc"].add_section(owner.id, r.id, {"v": 1})
        with pytest.raises(PermissionDenied):
            await s["report_svc"].acquire_lock(viewer.id, sec.id)

    async def test_owner_can_update_report(self, services):
        s = services
        owner = await seed_user(s["user_repo"], "owner")
        r = await s["report_svc"].create(owner.id, "Original")
        updated = await s["report_svc"].update(owner.id, r.id, title="New")
        assert updated.title == "New"

    async def test_editor_cannot_update_report(self, services):
        s = services
        owner = await seed_user(s["user_repo"], "owner")
        editor = await seed_user(s["user_repo"], "editor")
        r = await s["report_svc"].create(owner.id, "Report")
        await s["perm_svc"].grant_access(r.id, editor.id, "editor")
        with pytest.raises(PermissionDenied):
            await s["report_svc"].update(editor.id, r.id, title="Nope")


@pytest.mark.asyncio
class TestReportNotFound:
    """Verify that operations on nonexistent entities raise NotFound."""

    async def test_edit_nonexistent_section(self, services):
        s = services
        await seed_user(s["user_repo"], "u1")
        with pytest.raises(NotFound):
            await s["report_svc"].edit_section("u1", "no-such-section", {})

    async def test_delete_nonexistent_section(self, services):
        s = services
        await seed_user(s["user_repo"], "u1")
        with pytest.raises(NotFound):
            await s["report_svc"].delete_section("u1", "no-such-section")

    async def test_acquire_lock_nonexistent_section(self, services):
        s = services
        await seed_user(s["user_repo"], "u1")
        with pytest.raises(NotFound):
            await s["report_svc"].acquire_lock("u1", "no-such-section")


@pytest.mark.asyncio
class TestSectionLocking:
    """Verify that section locking prevents concurrent edits."""

    async def test_locked_section_rejects_other_editor(self, services):
        s = services
        u1 = await seed_user(s["user_repo"], "u1")
        u2 = await seed_user(s["user_repo"], "u2")
        r = await s["report_svc"].create(u1.id, "Report")
        await s["perm_svc"].grant_access(r.id, u2.id, "editor")
        sec = await s["report_svc"].add_section(u1.id, r.id, {"v": 1})
        await s["report_svc"].acquire_lock(u1.id, sec.id)
        with pytest.raises(Conflict):
            await s["report_svc"].edit_section(u2.id, sec.id, {"v": 2})

    async def test_lock_holder_can_still_edit(self, services):
        s = services
        u1 = await seed_user(s["user_repo"], "u1")
        r = await s["report_svc"].create(u1.id, "Report")
        sec = await s["report_svc"].add_section(u1.id, r.id, {"v": 1})
        await s["report_svc"].acquire_lock(u1.id, sec.id)
        updated = await s["report_svc"].edit_section(u1.id, sec.id, {"v": 2})
        assert updated.content_json == {"v": 2}
