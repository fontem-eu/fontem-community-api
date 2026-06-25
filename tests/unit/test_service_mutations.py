"""Tests for IssueService, PermissionService, and ModerationService —
verify access control, trust levels, and error behavior through public APIs.
"""
from __future__ import annotations

import pytest
from src.domain.moderation import Sanction
from src.services.exceptions import Conflict, NotFound, PermissionDenied
from tests.conftest import seed_user


# ── IssueService ───────────────────────────────────────────────

@pytest.mark.asyncio
class TestIssueServiceMutations:

    async def test_create_allows_any_signed_in_user(self, services):
        s = services
        user = await seed_user(s["user_repo"], "u1", trust_level="new_user")
        issue = await s["issue_svc"].create(user.id, "T", "body", "other", "company", "c1")
        assert issue.created_by == user.id

    async def test_create_allows_contributor(self, services):
        s = services
        user = await seed_user(s["user_repo"], "u1", trust_level="contributor")
        issue = await s["issue_svc"].create(user.id, "T", "body", "other", "company", "c1")
        assert issue.title == "T"
        assert issue.created_by == user.id

    async def test_create_unknown_user_denied(self, services):
        s = services
        with pytest.raises(PermissionDenied, match="User not found"):
            await s["issue_svc"].create("ghost-no-exist", "T", "body", "other", "", "")

    async def test_add_comment_not_found(self, services):
        s = services
        user = await seed_user(s["user_repo"], "u1")
        with pytest.raises(NotFound, match="iss-999"):
            await s["issue_svc"].add_comment(user.id, "iss-999", "hi")

    async def test_add_comment_on_closed_issue(self, services):
        s = services
        user = await seed_user(s["user_repo"], "u1", trust_level="contributor")
        mod = await seed_user(s["user_repo"], "mod", trust_level="moderator")
        issue = await s["issue_svc"].create(user.id, "T", "body", "other", "company", "c1")
        await s["issue_svc"].resolve(mod.id, issue.id, "closed")
        with pytest.raises(Conflict, match="closed"):
            await s["issue_svc"].add_comment(user.id, issue.id, "late comment")

    async def test_vote_not_found(self, services):
        s = services
        user = await seed_user(s["user_repo"], "u1")
        with pytest.raises(NotFound, match="iss-404"):
            await s["issue_svc"].vote(user.id, "iss-404", "up")

    async def test_resolve_requires_moderator(self, services):
        s = services
        user = await seed_user(s["user_repo"], "u1", trust_level="contributor")
        await seed_user(s["user_repo"], "mod", trust_level="moderator")
        issue = await s["issue_svc"].create(user.id, "T", "body", "other", "company", "c1")
        with pytest.raises(PermissionDenied, match="Moderator"):
            await s["issue_svc"].resolve(user.id, issue.id, "resolved")

    async def test_resolve_allows_moderator(self, services):
        s = services
        user = await seed_user(s["user_repo"], "u1", trust_level="contributor")
        mod = await seed_user(s["user_repo"], "mod", trust_level="moderator")
        issue = await s["issue_svc"].create(user.id, "T", "body", "other", "company", "c1")
        await s["issue_svc"].resolve(mod.id, issue.id, "resolved")

    async def test_resolve_not_found(self, services):
        s = services
        mod = await seed_user(s["user_repo"], "mod", trust_level="moderator")
        with pytest.raises(NotFound, match="iss-nope"):
            await s["issue_svc"].resolve(mod.id, "iss-nope", "resolved")

    async def test_add_comment_sets_parent_type_issue(self, services):
        s = services
        user = await seed_user(s["user_repo"], "u1", trust_level="contributor")
        issue = await s["issue_svc"].create(user.id, "T", "body", "other", "company", "c1")
        comment = await s["issue_svc"].add_comment(user.id, issue.id, "hello")
        assert comment.parent_type == "issue"
        assert comment.parent_id == issue.id
        assert comment.author_id == user.id


# ── PermissionService ──────────────────────────────────────────

@pytest.mark.asyncio
class TestPermissionServiceMutations:

    async def test_admin_role_bypasses_check(self, services):
        s = services
        admin = await seed_user(s["user_repo"], "admin1", trust_level="new_user", roles=["admin"])
        result = await s["perm_svc"].check(admin.id, "any-report", "owner")
        assert result is True

    async def test_admin_trust_bypasses_check(self, services):
        s = services
        admin = await seed_user(s["user_repo"], "admin2", trust_level="admin")
        result = await s["perm_svc"].check(admin.id, "any-report", "owner")
        assert result is True

    async def test_suspended_user_denied(self, services):
        s = services
        user = await seed_user(s["user_repo"], "u1")
        r = await s["report_svc"].create(user.id, "Test")
        sanction = Sanction(user_id=user.id, type="suspend", reason="test")
        await s["user_repo"].add_sanction(sanction)
        result = await s["perm_svc"].check(user.id, r.id, "viewer")
        assert result is False

    async def test_public_report_viewable_without_grant(self, services):
        s = services
        u1 = await seed_user(s["user_repo"], "u1")
        u2 = await seed_user(s["user_repo"], "u2")
        r = await s["report_svc"].create(u1.id, "Public Report")
        await s["report_svc"].update(u1.id, r.id, visibility="public_open")
        result = await s["perm_svc"].check(u2.id, r.id, "viewer")
        assert result is True

    async def test_public_report_not_editable_without_grant(self, services):
        s = services
        u1 = await seed_user(s["user_repo"], "u1")
        u2 = await seed_user(s["user_repo"], "u2")
        r = await s["report_svc"].create(u1.id, "Public Report")
        await s["report_svc"].update(u1.id, r.id, visibility="public_open")
        result = await s["perm_svc"].check(u2.id, r.id, "editor")
        assert result is False

    async def test_viewer_cannot_edit(self, services):
        s = services
        u1 = await seed_user(s["user_repo"], "u1")
        u2 = await seed_user(s["user_repo"], "u2")
        r = await s["report_svc"].create(u1.id, "R")
        await s["perm_svc"].grant_access(r.id, u2.id, "viewer")
        result = await s["perm_svc"].check(u2.id, r.id, "editor")
        assert result is False

    async def test_editor_can_edit(self, services):
        s = services
        u1 = await seed_user(s["user_repo"], "u1")
        u2 = await seed_user(s["user_repo"], "u2")
        r = await s["report_svc"].create(u1.id, "R")
        await s["perm_svc"].grant_access(r.id, u2.id, "editor")
        result = await s["perm_svc"].check(u2.id, r.id, "editor")
        assert result is True

    async def test_require_raises_with_user_and_report_in_message(self, services):
        s = services
        u1 = await seed_user(s["user_repo"], "u1")
        u2 = await seed_user(s["user_repo"], "u2")
        r = await s["report_svc"].create(u1.id, "R")
        with pytest.raises(PermissionDenied) as exc_info:
            await s["perm_svc"].require(u2.id, r.id, "editor")
        assert r.id in str(exc_info.value)
        assert "editor" in str(exc_info.value)


# ── ModerationService ─────────────────────────────────────────

@pytest.mark.asyncio
class TestModerationServiceMutations:

    async def test_flag_duplicate_raises_conflict(self, services):
        s = services
        user = await seed_user(s["user_repo"], "u1")
        await s["mod_svc"].flag(user.id, "report", "r1", "spam")
        with pytest.raises(Conflict, match="already flagged"):
            await s["mod_svc"].flag(user.id, "report", "r1", "spam")

    async def test_ban_requires_admin(self, services):
        s = services
        user = await seed_user(s["user_repo"], "u1")
        mod = await seed_user(s["user_repo"], "mod", trust_level="moderator")
        with pytest.raises(PermissionDenied):
            await s["mod_svc"].sanction(mod.id, user.id, "ban", "violation")

    async def test_ban_allowed_for_admin(self, services):
        s = services
        user = await seed_user(s["user_repo"], "u1")
        admin = await seed_user(s["user_repo"], "admin", trust_level="admin")
        result = await s["mod_svc"].sanction(admin.id, user.id, "ban", "violation")
        assert result.type == "ban"
        assert result.user_id == user.id

    async def test_mute_allowed_for_moderator(self, services):
        s = services
        user = await seed_user(s["user_repo"], "u1")
        mod = await seed_user(s["user_repo"], "mod", trust_level="moderator")
        result = await s["mod_svc"].sanction(mod.id, user.id, "mute", "spamming")
        assert result.type == "mute"

    async def test_get_queue_requires_moderator(self, services):
        s = services
        user = await seed_user(s["user_repo"], "u1", trust_level="contributor")
        with pytest.raises(PermissionDenied):
            await s["mod_svc"].get_queue(user.id, 10, 0)

    async def test_lift_requires_moderator(self, services):
        s = services
        user = await seed_user(s["user_repo"], "u1", trust_level="contributor")
        with pytest.raises(PermissionDenied):
            await s["mod_svc"].lift(user.id, "sanc-1")

    async def test_resolve_flags_requires_moderator(self, services):
        s = services
        user = await seed_user(s["user_repo"], "u1", trust_level="contributor")
        with pytest.raises(PermissionDenied):
            await s["mod_svc"].resolve_flags(user.id, "report", "r1", "dismiss")

    async def test_sanction_sets_applied_by(self, services):
        s = services
        user = await seed_user(s["user_repo"], "u1")
        mod = await seed_user(s["user_repo"], "mod", trust_level="moderator")
        result = await s["mod_svc"].sanction(mod.id, user.id, "warning", "bad behavior")
        assert result.applied_by == mod.id
        assert result.reason == "bad behavior"
