"""Tests targeting surviving mutmut mutants in ReportService.

Verifies:
- Exact permission role strings passed to PermissionService.require()
- DEFAULT_LOCK_TTL value used in acquire_lock
- Error messages contain the correct entity IDs
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from tests.conftest import seed_user
from src.services.exceptions import Conflict, NotFound
from src.services.report_service import DEFAULT_LOCK_TTL


class TestDefaultLockTTL:
    def test_lock_ttl_is_300(self):
        assert DEFAULT_LOCK_TTL == 300


@pytest.mark.asyncio
class TestPermissionRoleStrings:
    """Verify the exact role string passed to require() for each operation."""

    async def test_get_requires_viewer(self, services):
        s = services
        await seed_user(s["user_repo"], "u1")
        r = await s["report_svc"].create("u1", "T")
        with patch.object(s["perm_svc"], "require", new_callable=AsyncMock) as mock_req:
            await s["report_svc"].get("u1", r.id)
            mock_req.assert_called_once_with("u1", r.id, "viewer")

    async def test_update_requires_owner(self, services):
        s = services
        await seed_user(s["user_repo"], "u1")
        r = await s["report_svc"].create("u1", "T")
        with patch.object(s["perm_svc"], "require", new_callable=AsyncMock) as mock_req:
            await s["report_svc"].update("u1", r.id, title="New")
            mock_req.assert_called_once_with("u1", r.id, "owner")

    async def test_delete_requires_owner(self, services):
        s = services
        await seed_user(s["user_repo"], "u1")
        r = await s["report_svc"].create("u1", "T")
        with patch.object(s["perm_svc"], "require", new_callable=AsyncMock) as mock_req:
            await s["report_svc"].delete("u1", r.id)
            mock_req.assert_called_once_with("u1", r.id, "owner")

    async def test_add_section_requires_editor(self, services):
        s = services
        await seed_user(s["user_repo"], "u1")
        r = await s["report_svc"].create("u1", "T")
        with patch.object(s["perm_svc"], "require", new_callable=AsyncMock) as mock_req:
            await s["report_svc"].add_section("u1", r.id, {"text": "hi"})
            mock_req.assert_called_once_with("u1", r.id, "editor")

    async def test_edit_section_requires_editor(self, services):
        s = services
        await seed_user(s["user_repo"], "u1")
        r = await s["report_svc"].create("u1", "T")
        sec = await s["report_svc"].add_section("u1", r.id, {"text": "v1"})
        with patch.object(s["perm_svc"], "require", new_callable=AsyncMock) as mock_req:
            await s["report_svc"].edit_section("u1", sec.id, {"text": "v2"})
            mock_req.assert_called_once_with("u1", r.id, "editor")

    async def test_delete_section_requires_editor(self, services):
        s = services
        await seed_user(s["user_repo"], "u1")
        r = await s["report_svc"].create("u1", "T")
        sec = await s["report_svc"].add_section("u1", r.id, {"text": "v1"})
        with patch.object(s["perm_svc"], "require", new_callable=AsyncMock) as mock_req:
            await s["report_svc"].delete_section("u1", sec.id)
            mock_req.assert_called_once_with("u1", r.id, "editor")

    async def test_acquire_lock_requires_editor(self, services):
        s = services
        await seed_user(s["user_repo"], "u1")
        r = await s["report_svc"].create("u1", "T")
        sec = await s["report_svc"].add_section("u1", r.id, {"text": "v1"})
        with patch.object(s["perm_svc"], "require", new_callable=AsyncMock) as mock_req:
            await s["report_svc"].acquire_lock("u1", sec.id)
            mock_req.assert_called_once_with("u1", r.id, "editor")


@pytest.mark.asyncio
class TestErrorMessages:
    """Verify error messages contain the correct entity IDs."""

    async def test_edit_section_not_found_contains_id(self, services):
        s = services
        await seed_user(s["user_repo"], "u1")
        with pytest.raises(NotFound, match="sec-999"):
            await s["report_svc"].edit_section("u1", "sec-999", {})

    async def test_delete_section_not_found_contains_id(self, services):
        s = services
        await seed_user(s["user_repo"], "u1")
        with pytest.raises(NotFound, match="sec-888"):
            await s["report_svc"].delete_section("u1", "sec-888")

    async def test_acquire_lock_not_found_contains_id(self, services):
        s = services
        await seed_user(s["user_repo"], "u1")
        with pytest.raises(NotFound, match="sec-777"):
            await s["report_svc"].acquire_lock("u1", "sec-777")

    async def test_edit_section_conflict_contains_holder(self, services):
        s = services
        await seed_user(s["user_repo"], "u1")
        await seed_user(s["user_repo"], "u2")
        r = await s["report_svc"].create("u1", "T")
        # Grant u2 editor access
        await s["perm_svc"].grant_access(r.id, "u2", "editor")
        sec = await s["report_svc"].add_section("u1", r.id, {"text": "v1"})
        # u1 acquires lock
        await s["report_svc"].acquire_lock("u1", sec.id)
        # u2 tries to edit — should get Conflict with lock holder info
        with pytest.raises(Conflict, match="u1"):
            await s["report_svc"].edit_section("u2", sec.id, {"text": "v2"})


@pytest.mark.asyncio
class TestLockTTLUsage:
    """Verify DEFAULT_LOCK_TTL is passed to acquire_lock."""

    async def test_acquire_lock_uses_ttl_300(self, services):
        s = services
        await seed_user(s["user_repo"], "u1")
        r = await s["report_svc"].create("u1", "T")
        sec = await s["report_svc"].add_section("u1", r.id, {"text": "v1"})
        with patch.object(s["report_repo"], "acquire_lock", new_callable=AsyncMock, return_value=True) as mock_lock:
            await s["report_svc"].acquire_lock("u1", sec.id)
            mock_lock.assert_called_once_with(sec.id, "u1", 300)
