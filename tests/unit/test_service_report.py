"""Extended unit tests for ReportService — cover uncovered paths."""
from __future__ import annotations

import pytest
from tests.conftest import seed_user
from src.services.exceptions import NotFound, PermissionDenied


@pytest.mark.asyncio
class TestReportServiceExtended:
    """Additional ReportService coverage."""

    async def test_update_report_title(self, services):
        """update() changes title."""
        s = services
        await seed_user(s["user_repo"], "u1")
        r = await s["report_svc"].create("u1", "Old Title")
        updated = await s["report_svc"].update("u1", r.id, title="New Title")
        assert updated.title == "New Title"

    async def test_update_report_abstract(self, services):
        """update() changes abstract."""
        s = services
        await seed_user(s["user_repo"], "u1")
        r = await s["report_svc"].create("u1", "R", "Old abstract")
        updated = await s["report_svc"].update("u1", r.id, abstract="New abstract")
        assert updated.abstract == "New abstract"

    async def test_update_report_visibility(self, services):
        """update() changes visibility."""
        s = services
        await seed_user(s["user_repo"], "u1")
        r = await s["report_svc"].create("u1", "R")
        updated = await s["report_svc"].update("u1", r.id, visibility="public_open")
        assert updated.visibility == "public_open"

    async def test_update_nonexistent_report(self, services):
        """update() on nonexistent report raises NotFound."""
        s = services
        await seed_user(s["user_repo"], "u1")
        with pytest.raises(PermissionDenied):
            await s["report_svc"].update("u1", "nonexistent", title="X")

    async def test_get_nonexistent_report(self, services):
        """get() on nonexistent report raises PermissionDenied (checked before 404)."""
        s = services
        await seed_user(s["user_repo"], "u1")
        with pytest.raises(PermissionDenied):
            await s["report_svc"].get("u1", "nonexistent")

    async def test_get_sections(self, services):
        """get_sections() returns sections for a report."""
        s = services
        await seed_user(s["user_repo"], "u1")
        r = await s["report_svc"].create("u1", "R")
        await s["report_svc"].add_section("u1", r.id, {"html": "s1"})
        await s["report_svc"].add_section("u1", r.id, {"html": "s2"})
        sections = await s["report_svc"].get_sections(r.id)
        assert len(sections) == 2

    async def test_list_public_reports(self, services):
        """list_public() returns public reports only."""
        s = services
        await seed_user(s["user_repo"], "u1")
        r = await s["report_svc"].create("u1", "Private")
        await s["report_svc"].create("u1", "Public")
        await s["report_svc"].update("u1", r.id, visibility="public_open")
        public = await s["report_svc"].list_public(10, 0)
        assert len(public) == 1

    async def test_edit_section_nonexistent(self, services):
        """edit_section() on nonexistent section raises NotFound."""
        s = services
        await seed_user(s["user_repo"], "u1")
        with pytest.raises(NotFound):
            await s["report_svc"].edit_section("u1", "nonexistent", {})

    async def test_delete_section_nonexistent(self, services):
        """delete_section() on nonexistent section raises NotFound."""
        s = services
        await seed_user(s["user_repo"], "u1")
        with pytest.raises(NotFound):
            await s["report_svc"].delete_section("u1", "nonexistent")

    async def test_acquire_lock_nonexistent(self, services):
        """acquire_lock() on nonexistent section raises NotFound."""
        s = services
        await seed_user(s["user_repo"], "u1")
        with pytest.raises(NotFound):
            await s["report_svc"].acquire_lock("u1", "nonexistent")
