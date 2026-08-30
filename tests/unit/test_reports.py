"""
Report lifecycle unit tests (RPT-01 through RPT-10).
InMemory repos — 0 I/O.
"""
from __future__ import annotations

import time

import pytest

from tests.conftest import seed_user, _stable_uuid


@pytest.mark.asyncio
class TestReports:
    # RPT-01: Creating report makes creator the owner
    async def test_create_makes_owner(self, services):
        s = services
        await seed_user(s["user_repo"], "user-1")

        report = await s["report_svc"].create(_stable_uuid("user-1"), "My Report")
        assert report.id is not None
        assert report.created_by == _stable_uuid("user-1")
        assert await s["perm_svc"].check(_stable_uuid("user-1"), report.id, "owner")

    # RPT-02: Adding section increments sort order
    async def test_saving_keeps_the_previous_document(self, services):
        """The substrate the revision history is built on: a save that
        forgets its predecessor cannot be reviewed or reverted."""
        s = services
        await seed_user(s["user_repo"], "user-1")

        report = await s["report_svc"].create(_stable_uuid("user-1"), "Report")
        await s["report_svc"].save_document(_stable_uuid("user-1"), report.id, {"text": "original"})
        await s["report_svc"].save_document(_stable_uuid("user-1"), report.id, {"text": "updated"})

        sections = await s["report_svc"].get_sections(report.id)
        versions = await s["report_repo"].get_versions(sections[0].id, 10)
        assert len(versions) >= 1
        assert versions[0].content_json == {"text": "original"}

    async def test_version_history_order(self, services):
        s = services
        await seed_user(s["user_repo"], "user-1")

        report = await s["report_svc"].create(_stable_uuid("user-1"), "Report")
        for v in (1, 2, 3):
            await s["report_svc"].save_document(_stable_uuid("user-1"), report.id, {"v": v})

        sections = await s["report_svc"].get_sections(report.id)
        versions = await s["report_repo"].get_versions(sections[0].id, 10)
        assert len(versions) >= 2
        # Most recent version first
        assert versions[0].saved_at >= versions[-1].saved_at

    async def test_list_includes_owned(self, services):
        s = services
        await seed_user(s["user_repo"], "user-1")

        await s["report_svc"].create(_stable_uuid("user-1"), "Report 1")
        await s["report_svc"].create(_stable_uuid("user-1"), "Report 2")

        reports = await s["report_svc"].list_my_reports(_stable_uuid("user-1"), 10, 0)
        assert len(reports) == 2

    # RPT-10: Deleting report cascades to sections
    async def test_delete_cascades(self, services):
        s = services
        await seed_user(s["user_repo"], "user-1")

        report = await s["report_svc"].create(_stable_uuid("user-1"), "Report")
        await s["report_svc"].save_document(_stable_uuid("user-1"), report.id, {"x": 1})
        await s["report_svc"].delete(_stable_uuid("user-1"), report.id)

        assert await s["report_repo"].get_by_id(report.id) is None
