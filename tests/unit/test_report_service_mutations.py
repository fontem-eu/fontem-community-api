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

    async def test_editor_can_save_the_document(self, services):
        s = services
        owner = await seed_user(s["user_repo"], "owner")
        editor = await seed_user(s["user_repo"], "editor")
        r = await s["report_svc"].create(owner.id, "Report")
        await s["perm_svc"].grant_access(r.id, editor.id, "editor")
        await s["report_svc"].save_document(editor.id, r.id, {"text": "hi"}, None)
        sections = await s["report_svc"].get_sections(r.id)
        assert sections[0].content_json == {"text": "hi"}

    async def test_viewer_cannot_save_the_document(self, services):
        s = services
        owner = await seed_user(s["user_repo"], "owner")
        viewer = await seed_user(s["user_repo"], "viewer")
        r = await s["report_svc"].create(owner.id, "Report")
        await s["perm_svc"].grant_access(r.id, viewer.id, "viewer")
        with pytest.raises(PermissionDenied):
            await s["report_svc"].save_document(viewer.id, r.id, {"text": "hi"}, None)

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
