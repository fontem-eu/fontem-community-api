"""
Issue unit tests (ISS-01 through ISS-07).
InMemory repos — 0 I/O.
"""
from __future__ import annotations

import pytest

from tests.conftest import seed_user

from src.services.exceptions import PermissionDenied


@pytest.mark.asyncio
class TestIssues:
    # ISS-01: Creating issue sets status to 'open'
    async def test_create_sets_open(self, services):
        s = services
        await seed_user(s["user_repo"], "user-1", trust_level="contributor")

        issue = await s["issue_svc"].create(
            "user-1", "Bad data", "Country is wrong",
            "incorrect_data", "Company", "gmr-123",
        )
        assert issue.status == "open"
        assert issue.id is not None

    # ISS-02: Adding comment appends to thread
    async def test_add_comment(self, services):
        s = services
        await seed_user(s["user_repo"], "user-1", trust_level="contributor")
        await seed_user(s["user_repo"], "user-2", trust_level="commenter")

        issue = await s["issue_svc"].create(
            "user-1", "Issue", "Body",
            "incorrect_data", "Company", "gmr-123",
        )
        comment = await s["issue_svc"].add_comment(
            "user-2", issue.id, "I agree, this is wrong",
        )
        assert comment.id is not None
        assert comment.author_id == "user-2"

    # ISS-03: Voting updates count
    async def test_vote_updates_count(self, services):
        s = services
        await seed_user(s["user_repo"], "user-1", trust_level="contributor")
        await seed_user(s["user_repo"], "user-2", trust_level="commenter")

        issue = await s["issue_svc"].create(
            "user-1", "Issue", "Body",
            "incorrect_data", "Company", "gmr-123",
        )
        await s["issue_svc"].vote("user-2", issue.id, "up")
        count = await s["issue_repo"].get_vote_count(issue.id)
        assert count == 1

    # ISS-04: Double-voting by same user is idempotent
    async def test_double_vote_idempotent(self, services):
        s = services
        await seed_user(s["user_repo"], "user-1", trust_level="contributor")

        issue = await s["issue_svc"].create(
            "user-1", "Issue", "Body",
            "incorrect_data", "Company", "gmr-123",
        )
        await s["issue_svc"].vote("user-1", issue.id, "up")
        await s["issue_svc"].vote("user-1", issue.id, "up")
        count = await s["issue_repo"].get_vote_count(issue.id)
        assert count == 1

    # ISS-05: Only moderators can resolve
    async def test_resolve_requires_moderator(self, services):
        s = services
        await seed_user(s["user_repo"], "user-1", trust_level="contributor")

        issue = await s["issue_svc"].create(
            "user-1", "Issue", "Body",
            "incorrect_data", "Company", "gmr-123",
        )
        with pytest.raises(PermissionDenied):
            await s["issue_svc"].resolve("user-1", issue.id, "resolved")

    # ISS-05b: Moderator can resolve
    async def test_moderator_can_resolve(self, services):
        s = services
        await seed_user(s["user_repo"], "user-1", trust_level="contributor")
        await seed_user(s["user_repo"], "mod-1", trust_level="moderator", roles=["moderator"])

        issue = await s["issue_svc"].create(
            "user-1", "Issue", "Body",
            "incorrect_data", "Company", "gmr-123",
        )
        await s["issue_svc"].resolve("mod-1", issue.id, "resolved")
        updated = await s["issue_repo"].get_by_id(issue.id)
        assert updated.status == "resolved"

    # ISS-06: List for entity filters correctly
    async def test_list_for_entity(self, services):
        s = services
        await seed_user(s["user_repo"], "user-1", trust_level="contributor")

        await s["issue_svc"].create(
            "user-1", "Issue A", "Body",
            "incorrect_data", "Company", "gmr-111",
        )
        await s["issue_svc"].create(
            "user-1", "Issue B", "Body",
            "incorrect_data", "Company", "gmr-222",
        )
        result = await s["issue_svc"].list_for_entity("Company", "gmr-111", 10, 0)
        assert len(result) == 1
        assert result[0].entity_id == "gmr-111"

    # ISS-07: Closed issues cannot receive comments
    async def test_closed_issue_no_comments(self, services):
        s = services
        await seed_user(s["user_repo"], "user-1", trust_level="contributor")
        await seed_user(s["user_repo"], "mod-1", roles=["moderator"])

        issue = await s["issue_svc"].create(
            "user-1", "Issue", "Body",
            "incorrect_data", "Company", "gmr-123",
        )
        await s["issue_svc"].resolve("mod-1", issue.id, "closed")

        with pytest.raises(Exception):  # Conflict or PermissionDenied
            await s["issue_svc"].add_comment("user-1", issue.id, "Late comment")
