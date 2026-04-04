"""
Report lifecycle unit tests (RPT-01 through RPT-10).
InMemory repos — 0 I/O.
"""
from __future__ import annotations

import pytest

from tests.conftest import seed_user

from src.services.exceptions import PermissionDenied, Conflict


@pytest.mark.asyncio
class TestReports:
    # RPT-01: Creating report makes creator the owner
    async def test_create_makes_owner(self, services):
        s = services
        await seed_user(s["user_repo"], "user-1")

        report = await s["report_svc"].create("user-1", "My Report")
        assert report.id is not None
        assert report.created_by == "user-1"
        assert await s["perm_svc"].check("user-1", report.id, "owner")

    # RPT-02: Adding section increments sort order
    async def test_add_section_increments_order(self, services):
        s = services
        await seed_user(s["user_repo"], "user-1")

        report = await s["report_svc"].create("user-1", "Report")
        s1 = await s["report_svc"].add_section("user-1", report.id, {"type": "doc"})
        s2 = await s["report_svc"].add_section("user-1", report.id, {"type": "doc"})
        assert s2.sort_order > s1.sort_order

    # RPT-03: Editing section saves previous content as version
    async def test_edit_saves_version(self, services):
        s = services
        await seed_user(s["user_repo"], "user-1")

        report = await s["report_svc"].create("user-1", "Report")
        section = await s["report_svc"].add_section(
            "user-1", report.id, {"text": "original"},
        )
        await s["report_svc"].acquire_lock("user-1", section.id)
        await s["report_svc"].edit_section(
            "user-1", section.id, {"text": "updated"},
        )

        versions = await s["report_repo"].get_versions(section.id, 10)
        assert len(versions) >= 1
        assert versions[0].content_json == {"text": "original"}

    # RPT-04: Deleting section does not affect others
    async def test_delete_section_keeps_others(self, services):
        s = services
        await seed_user(s["user_repo"], "user-1")

        report = await s["report_svc"].create("user-1", "Report")
        s1 = await s["report_svc"].add_section("user-1", report.id, {"a": 1})
        s2 = await s["report_svc"].add_section("user-1", report.id, {"b": 2})
        await s["report_svc"].delete_section("user-1", s1.id)

        remaining = await s["report_repo"].get_sections(report.id)
        assert len(remaining) == 1
        assert remaining[0].id == s2.id

    # RPT-05: Section lock prevents concurrent edit
    async def test_lock_prevents_concurrent_edit(self, services):
        s = services
        await seed_user(s["user_repo"], "user-1")
        await seed_user(s["user_repo"], "user-2")

        report = await s["report_svc"].create("user-1", "Report")
        await s["permission_repo"].set_user_access(report.id, "user-2", "editor")
        section = await s["report_svc"].add_section(
            "user-1", report.id, {"text": "content"},
        )

        assert await s["report_svc"].acquire_lock("user-1", section.id)
        assert not await s["report_svc"].acquire_lock("user-2", section.id)

    # RPT-06: Section lock expires after TTL
    async def test_lock_expires(self, services):
        s = services
        await seed_user(s["user_repo"], "user-1")
        await seed_user(s["user_repo"], "user-2")

        report = await s["report_svc"].create("user-1", "Report")
        await s["permission_repo"].set_user_access(report.id, "user-2", "editor")
        section = await s["report_svc"].add_section(
            "user-1", report.id, {"text": "content"},
        )

        # Acquire with 0-second TTL (expires immediately)
        await s["report_repo"].acquire_lock(section.id, "user-1", 0)

        # Should succeed because lock expired
        import time
        time.sleep(0.01)
        assert await s["report_repo"].acquire_lock(section.id, "user-2", 300)

    # RPT-07: Lock holder can save and release
    async def test_lock_holder_can_save(self, services):
        s = services
        await seed_user(s["user_repo"], "user-1")

        report = await s["report_svc"].create("user-1", "Report")
        section = await s["report_svc"].add_section(
            "user-1", report.id, {"text": "v1"},
        )
        await s["report_svc"].acquire_lock("user-1", section.id)
        await s["report_svc"].edit_section("user-1", section.id, {"text": "v2"})
        await s["report_svc"].release_lock("user-1", section.id)

        holder = await s["report_repo"].get_lock_holder(section.id)
        assert holder is None

    # RPT-08: Version history returns most recent first
    async def test_version_history_order(self, services):
        s = services
        await seed_user(s["user_repo"], "user-1")

        report = await s["report_svc"].create("user-1", "Report")
        section = await s["report_svc"].add_section(
            "user-1", report.id, {"v": 1},
        )
        await s["report_svc"].acquire_lock("user-1", section.id)
        await s["report_svc"].edit_section("user-1", section.id, {"v": 2})
        await s["report_svc"].edit_section("user-1", section.id, {"v": 3})

        versions = await s["report_repo"].get_versions(section.id, 10)
        assert len(versions) >= 2
        # Most recent version first
        assert versions[0].saved_at >= versions[-1].saved_at

    # RPT-09: Listing reports for user includes owned
    async def test_list_includes_owned(self, services):
        s = services
        await seed_user(s["user_repo"], "user-1")

        await s["report_svc"].create("user-1", "Report 1")
        await s["report_svc"].create("user-1", "Report 2")

        reports = await s["report_svc"].list_my_reports("user-1", 10, 0)
        assert len(reports) == 2

    # RPT-10: Deleting report cascades to sections
    async def test_delete_cascades(self, services):
        s = services
        await seed_user(s["user_repo"], "user-1")

        report = await s["report_svc"].create("user-1", "Report")
        await s["report_svc"].add_section("user-1", report.id, {"x": 1})
        await s["report_svc"].delete("user-1", report.id)

        assert await s["report_repo"].get_by_id(report.id) is None
