"""
Report lifecycle unit tests (RPT-01 through RPT-10).
InMemory repos — 0 I/O.
"""
from __future__ import annotations

import time

import pytest

from src.services.exceptions import Conflict

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
        first = await s["report_svc"].save_document(
            _stable_uuid("user-1"), report.id, {"text": "original"}, None)
        second = await s["report_svc"].save_document(
            _stable_uuid("user-1"), report.id, {"text": "updated"}, first.id)

        assert second.parent_id == first.id
        older = await s["report_repo"].get_revision(first.id)
        assert older.content_json == {"text": "original"}

    async def test_the_chain_is_ordered_newest_first(self, services):
        s = services
        await seed_user(s["user_repo"], "user-1")

        report = await s["report_svc"].create(_stable_uuid("user-1"), "Report")
        head = None
        for v in (1, 2, 3):
            head = (await s["report_svc"].save_document(
                _stable_uuid("user-1"), report.id, {"v": v}, head)).id

        revisions = await s["report_repo"].list_revisions(report.id, 10)
        assert [r.content_json["v"] for r in revisions] == [3, 2, 1]
        # And main points at the newest of them.
        branch = await s["report_repo"].get_branch(report.id, None)
        assert branch.head_revision_id == revisions[0].id == head

    async def test_an_identical_save_adds_no_revision(self, services):
        """Autosave re-sends the same document constantly; the history
        should carry edits, not keystroke timers."""
        s = services
        await seed_user(s["user_repo"], "user-1")

        report = await s["report_svc"].create(_stable_uuid("user-1"), "Report")
        first = await s["report_svc"].save_document(
            _stable_uuid("user-1"), report.id, {"text": "same"}, None)
        again = await s["report_svc"].save_document(
            _stable_uuid("user-1"), report.id, {"text": "same"}, first.id)

        assert again.id == first.id
        assert len(await s["report_repo"].list_revisions(report.id, 10)) == 1

    async def test_a_stale_save_is_refused_with_the_current_state(self, services):
        """The lost-widgets bug, as a test. Two writers, one baseline: the
        second must be told, not silently win."""
        s = services
        await seed_user(s["user_repo"], "user-1")

        report = await s["report_svc"].create(_stable_uuid("user-1"), "Report")
        base = await s["report_svc"].save_document(
            _stable_uuid("user-1"), report.id, {"text": "base"}, None)
        await s["report_svc"].save_document(
            _stable_uuid("user-1"), report.id, {"text": "someone else's work"}, base.id)

        with pytest.raises(Conflict) as caught:
            await s["report_svc"].save_document(
                _stable_uuid("user-1"), report.id, {"text": "written on a stale buffer"},
                base.id)

        # The refusal carries what it refused to overwrite, so the editor
        # can show the difference rather than just failing.
        assert caught.value.payload["current_doc"] == {
            "text": "someone else's work"}
        head = await s["report_svc"].document_head(report.id)
        assert head.content_json == {"text": "someone else's work"}

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
        await s["report_svc"].save_document(_stable_uuid("user-1"), report.id, {"x": 1}, None)
        await s["report_svc"].delete(_stable_uuid("user-1"), report.id)

        assert await s["report_repo"].get_by_id(report.id) is None
