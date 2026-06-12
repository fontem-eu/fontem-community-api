"""Extended unit tests for ReportService — cover uncovered paths."""
from __future__ import annotations

import pytest
from tests.conftest import seed_user, _stable_uuid
from src.services.exceptions import NotFound


@pytest.mark.asyncio
class TestReportServiceExtended:
    """Additional ReportService coverage."""

    async def test_update_report_title(self, services):
        """update() changes title."""
        s = services
        await seed_user(s["user_repo"], "u1")
        r = await s["report_svc"].create(_stable_uuid("u1"), "Old Title")
        updated = await s["report_svc"].update(_stable_uuid("u1"), r.id, title="New Title")
        assert updated.title == "New Title"

    async def test_update_report_abstract(self, services):
        """update() changes abstract."""
        s = services
        await seed_user(s["user_repo"], "u1")
        r = await s["report_svc"].create(_stable_uuid("u1"), "R", "Old abstract")
        updated = await s["report_svc"].update(_stable_uuid("u1"), r.id, abstract="New abstract")
        assert updated.abstract == "New abstract"

    async def test_update_report_visibility(self, services):
        """update() changes visibility."""
        s = services
        await seed_user(s["user_repo"], "u1")
        r = await s["report_svc"].create(_stable_uuid("u1"), "R")
        updated = await s["report_svc"].update(_stable_uuid("u1"), r.id, visibility="public_open")
        assert updated.visibility == "public_open"

    async def test_update_nonexistent_report(self, services):
        """update() on nonexistent report raises NotFound.

        The authz migration loads the report first so the policy can
        see its visibility/owner — that turns the old PermissionDenied
        (perm-check-before-load) into a NotFound. Cleaner contract,
        and the same 404 the router was already returning anyway.
        """
        s = services
        await seed_user(s["user_repo"], "u1")
        with pytest.raises(NotFound):
            await s["report_svc"].update(_stable_uuid("u1"), "nonexistent", title="X")

    async def test_get_nonexistent_report(self, services):
        """get() on nonexistent report raises NotFound (load-first)."""
        s = services
        await seed_user(s["user_repo"], "u1")
        with pytest.raises(NotFound):
            await s["report_svc"].get(_stable_uuid("u1"), "nonexistent")

    async def test_get_sections(self, services):
        """get_sections() returns sections for a report."""
        s = services
        await seed_user(s["user_repo"], "u1")
        r = await s["report_svc"].create(_stable_uuid("u1"), "R")
        await s["report_svc"].add_section(_stable_uuid("u1"), r.id, {"html": "s1"})
        await s["report_svc"].add_section(_stable_uuid("u1"), r.id, {"html": "s2"})
        sections = await s["report_svc"].get_sections(r.id)
        assert len(sections) == 2

    async def test_list_public_reports(self, services):
        """list_public() returns public reports only."""
        s = services
        await seed_user(s["user_repo"], "u1")
        r = await s["report_svc"].create(_stable_uuid("u1"), "Private")
        await s["report_svc"].create(_stable_uuid("u1"), "Public")
        await s["report_svc"].update(_stable_uuid("u1"), r.id, visibility="public_open")
        public = await s["report_svc"].list_public(10, 0)
        assert len(public) == 1

    async def test_edit_section_nonexistent(self, services):
        """edit_section() on nonexistent section raises NotFound."""
        s = services
        await seed_user(s["user_repo"], "u1")
        with pytest.raises(NotFound):
            await s["report_svc"].edit_section(_stable_uuid("u1"), "nonexistent", {})

    async def test_delete_section_nonexistent(self, services):
        """delete_section() on nonexistent section raises NotFound."""
        s = services
        await seed_user(s["user_repo"], "u1")
        with pytest.raises(NotFound):
            await s["report_svc"].delete_section(_stable_uuid("u1"), "nonexistent")

    async def test_acquire_lock_nonexistent(self, services):
        """acquire_lock() on nonexistent section raises NotFound."""
        s = services
        await seed_user(s["user_repo"], "u1")
        with pytest.raises(NotFound):
            await s["report_svc"].acquire_lock(_stable_uuid("u1"), "nonexistent")
