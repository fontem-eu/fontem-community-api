"""Additional issue service tests for coverage."""
from __future__ import annotations

import pytest
from tests.conftest import seed_user
from src.services.exceptions import PermissionDenied, Conflict, NotFound


@pytest.mark.asyncio
class TestIssueServiceExtra:
    """Cover issue creation, comments, voting, closing, resolution."""

    async def test_new_user_cannot_create_issue(self, services):
        """Users with trust_level=new_user cannot create issues."""
        s = services
        await seed_user(s["user_repo"], "newbie", trust_level="new_user")
        with pytest.raises(PermissionDenied):
            await s["issue_svc"].create(
                "newbie", "Bug", "incorrect_data", "company", "x", "desc"
            )

    async def test_contributor_can_create_issue(self, services):
        """Contributors can create issues."""
        s = services
        await seed_user(s["user_repo"], "contrib", trust_level="contributor")
        issue = await s["issue_svc"].create(
            "contrib", "Bug", "incorrect_data", "company", "x", "description"
        )
        assert issue.title == "Bug"
        assert issue.id is not None

    async def test_add_comment_to_issue(self, services):
        """Adding a comment to an open issue succeeds."""
        s = services
        await seed_user(s["user_repo"], "user-1", trust_level="contributor")
        issue = await s["issue_svc"].create("user-1", "I1", "other", "company", "x", "b")
        comment = await s["issue_svc"].add_comment("user-1", issue.id, "A comment")
        assert comment.body_md == "A comment"

    async def test_vote_on_issue(self, services):
        """Voting on an issue increments the vote count."""
        s = services
        await seed_user(s["user_repo"], "user-1", trust_level="contributor")
        issue = await s["issue_svc"].create("user-1", "I1", "other", "company", "x", "b")
        await s["issue_svc"].vote("user-1", issue.id, "up")
        count = await s["issue_repo"].get_vote_count(issue.id)
        assert count >= 1

    async def test_resolve_issue_requires_moderator(self, services):
        """Only moderators can resolve issues."""
        s = services
        await seed_user(s["user_repo"], "user-1", trust_level="contributor")
        issue = await s["issue_svc"].create("user-1", "I1", "other", "company", "x", "b")
        with pytest.raises(PermissionDenied):
            await s["issue_svc"].resolve("user-1", issue.id, "resolved")

    async def test_moderator_can_resolve(self, services):
        """Moderators can resolve issues."""
        s = services
        await seed_user(s["user_repo"], "user-1", trust_level="contributor")
        await seed_user(s["user_repo"], "mod-1", trust_level="moderator", roles=["moderator"])
        issue = await s["issue_svc"].create("user-1", "I1", "other", "company", "x", "b")
        await s["issue_svc"].resolve("mod-1", issue.id, "resolved")
        updated = await s["issue_repo"].get_by_id(issue.id)
        assert updated.status == "resolved"

    async def test_comment_on_nonexistent_issue(self, services):
        """Commenting on non-existent issue raises NotFound."""
        s = services
        await seed_user(s["user_repo"], "user-1", trust_level="contributor")
        with pytest.raises(NotFound):
            await s["issue_svc"].add_comment("user-1", "ghost", "hello")
