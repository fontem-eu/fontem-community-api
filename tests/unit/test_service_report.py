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

    async def test_update_report_nuts_region(self, services):
        """update() sets, validates, preserves and clears the region tag."""
        s = services
        await seed_user(s["user_repo"], "u1")
        r = await s["report_svc"].create(_stable_uuid("u1"), "R")
        u = await s["report_svc"].update(_stable_uuid("u1"), r.id, nuts_region="pt170")
        assert u.nuts_region == "PT170"
        # a partial update (region not passed) leaves it intact
        u = await s["report_svc"].update(_stable_uuid("u1"), r.id, title="X")
        assert u.nuts_region == "PT170"
        # a malformed code is ignored
        u = await s["report_svc"].update(_stable_uuid("u1"), r.id, nuts_region="!!bad")
        assert u.nuts_region == "PT170"
        # empty clears it
        u = await s["report_svc"].update(_stable_uuid("u1"), r.id, nuts_region="")
        assert u.nuts_region == ""

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
        s = services
        await seed_user(s["user_repo"], "u1")
        r = await s["report_svc"].create(_stable_uuid("u1"), "R")
        await s["report_svc"].save_document(_stable_uuid("u1"), r.id, {"text": "body"})
        sections = await s["report_svc"].get_sections(r.id)
        assert len(sections) == 1
        assert sections[0].content_json == {"text": "body"}

    async def test_list_public_reports(self, services):
        """list_public() returns public reports only."""
        s = services
        await seed_user(s["user_repo"], "u1")
        r = await s["report_svc"].create(_stable_uuid("u1"), "Private")
        await s["report_svc"].create(_stable_uuid("u1"), "Public")
        await s["report_svc"].update(_stable_uuid("u1"), r.id, visibility="public_open")
        public = await s["report_svc"].list_public(10, 0)
        assert len(public) == 1
