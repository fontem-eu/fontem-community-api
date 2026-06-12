"""HTTP-level tests for group endpoints."""
from __future__ import annotations

import asyncio
import pytest
from tests.conftest import _stable_uuid, make_headers, seed_user


@pytest.mark.asyncio
class TestGroupAPI:
    """Cover /groups endpoints."""

    async def _setup(self, services):
        await seed_user(services["user_repo"], "user-1")

    def test_create_group(self, client, services):
        """POST /groups creates a group."""
        asyncio.get_event_loop().run_until_complete(self._setup(services))
        resp = client.post(
            "/groups",
            json={"name": "Team Alpha", "description": "Test group"},
            headers=make_headers("user-1"),
        )
        assert resp.status_code == 201
        assert resp.json()["name"] == "Team Alpha"

    def test_get_group(self, client, services):
        """GET /groups/:id returns group with members."""
        asyncio.get_event_loop().run_until_complete(self._setup(services))
        h = make_headers("user-1")
        g = client.post("/groups", json={"name": "G1"}, headers=h).json()
        resp = client.get(f"/groups/{g['id']}", headers=h)
        assert resp.status_code == 200
        assert resp.json()["name"] == "G1"

    def test_add_member(self, client, services):
        """POST /groups/:id/members adds a member."""
        asyncio.get_event_loop().run_until_complete(self._setup(services))
        h = make_headers("user-1")
        g = client.post("/groups", json={"name": "G1"}, headers=h).json()
        resp = client.post(
            f"/groups/{g['id']}/members",
            json={"user_id": _stable_uuid("user-1")},
            headers=h,
        )
        assert resp.status_code in (200, 201)

    def test_remove_member(self, client, services):
        """DELETE /groups/:id/members/:uid removes a member."""
        asyncio.get_event_loop().run_until_complete(self._setup(services))
        h = make_headers("user-1")
        g = client.post("/groups", json={"name": "G1"}, headers=h).json()
        client.post(f"/groups/{g['id']}/members", json={"user_id": _stable_uuid("user-1")}, headers=h)
        resp = client.delete(f"/groups/{g["id"]}/members/{_stable_uuid("user-1")}", headers=h)
        assert resp.status_code == 204


@pytest.mark.asyncio
class TestGroupsIDORRegression:
    """Regression: the 2026-06-11 security review confirmed live on
    staging that any authenticated user could (1) self-add to any
    group, (2) read group-shared private resources as a result, and
    (3) remove the creator from their own group. The fix is the
    AuthorizationService gating membership ops on group ownership
    (see src/services/authz/policy.py:GROUPS_MANAGE_MEMBERS). These
    tests pin the closure of each step of the exploit chain.
    """

    async def _setup_two_users(self, services):
        await seed_user(services["user_repo"], "alice")
        await seed_user(services["user_repo"], "mallory")

    def test_non_creator_cannot_add_themselves_to_a_group(self, client, services):
        """Step 1 of the exploit: mallory POSTs /groups/<gid>/members
        with her own user_id, expects 403 from the policy."""
        asyncio.get_event_loop().run_until_complete(self._setup_two_users(services))
        alice = make_headers("alice")
        mallory = make_headers("mallory")

        # Alice creates the group — owns it.
        gid = client.post("/groups", json={"name": "alice-only"}, headers=alice).json()["id"]

        # Mallory tries to self-add via the IDOR.
        resp = client.post(
            f"/groups/{gid}/members",
            json={"user_id": _stable_uuid("mallory")},
            headers=mallory,
        )
        assert resp.status_code == 403, resp.text
        assert "not owner" in resp.json()["detail"].lower()

    def test_non_creator_cannot_remove_the_creator(self, client, services):
        """Step 3 of the exploit: mallory tries to remove alice from
        alice's own group (locking alice out). Must be denied even
        if mallory had managed to slip in via some other path."""
        asyncio.get_event_loop().run_until_complete(self._setup_two_users(services))
        alice = make_headers("alice")
        mallory = make_headers("mallory")

        gid = client.post("/groups", json={"name": "alice-only"}, headers=alice).json()["id"]

        # Even with mallory directly trying to remove alice — 403.
        resp = client.delete(
            f"/groups/{gid}/members/{_stable_uuid('alice')}",
            headers=mallory,
        )
        assert resp.status_code == 403, resp.text

    def test_non_creator_cannot_read_member_list(self, client, services):
        """Step 2's prerequisite — disclosure of membership to
        non-members was its own finding in the review. The
        /groups/{id}/members endpoint must 403 non-owners."""
        asyncio.get_event_loop().run_until_complete(self._setup_two_users(services))
        alice = make_headers("alice")
        mallory = make_headers("mallory")

        gid = client.post("/groups", json={"name": "alice-only"}, headers=alice).json()["id"]

        # Alice (owner) can read.
        own = client.get(f"/groups/{gid}/members", headers=alice)
        assert own.status_code == 200
        # Mallory (not owner) gets 403.
        other = client.get(f"/groups/{gid}/members", headers=mallory)
        assert other.status_code == 403, other.text

    def test_creator_can_still_manage_their_group(self, client, services):
        """Belt-and-braces: the IDOR fix doesn't accidentally lock
        the legitimate owner out."""
        asyncio.get_event_loop().run_until_complete(self._setup_two_users(services))
        alice = make_headers("alice")

        gid = client.post("/groups", json={"name": "alice-team"}, headers=alice).json()["id"]
        add = client.post(
            f"/groups/{gid}/members",
            json={"user_id": _stable_uuid("mallory")},
            headers=alice,
        )
        assert add.status_code == 201
        remove = client.delete(
            f"/groups/{gid}/members/{_stable_uuid('mallory')}",
            headers=alice,
        )
        assert remove.status_code == 204

    def test_add_nonexistent_user_is_404_not_500(self, client, services):
        """Companion finding 7 in the review: adding a non-existent
        user_id used to land 500 from an asyncpg FK violation; the
        GroupService now resolves the target user first and returns
        a clean 404."""
        asyncio.get_event_loop().run_until_complete(self._setup_two_users(services))
        alice = make_headers("alice")
        gid = client.post("/groups", json={"name": "x"}, headers=alice).json()["id"]
        resp = client.post(
            f"/groups/{gid}/members",
            json={"user_id": "00000000-0000-4000-8000-000000000000"},
            headers=alice,
        )
        assert resp.status_code == 404, resp.text
        assert "User" in resp.json()["detail"]
