"""HTTP-level tests for router coverage gaps.

Covers endpoints in reports, sharing, issues, groups, users, and moderation
routers that are not exercised by existing test files.
"""
from __future__ import annotations

import asyncio

import pytest
from tests.conftest import make_headers, seed_user, _stable_uuid


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    """Run an async coroutine from sync test code."""
    return asyncio.get_event_loop().run_until_complete(coro)


def _seed(services, user_id="user-1", trust_level="contributor", roles=None):
    _run(seed_user(services["user_repo"], user_id, trust_level=trust_level, roles=roles))


def _create_report(client, headers, title="Test Report"):
    """Helper: create a report and return its id."""
    resp = client.post("/reports", json={"title": title}, headers=headers)
    assert resp.status_code == 201
    return resp.json()["id"]


def _create_issue(client, headers):
    """Helper: create an issue and return its id."""
    resp = client.post("/issues", json={
        "title": "Bad data",
        "body": "Entity X has wrong revenue",
        "issue_type": "incorrect_data",
        "entity_type": "company",
        "entity_id": "comp-1",
    }, headers=headers)
    assert resp.status_code == 201
    return resp.json()["id"]


# ---------------------------------------------------------------------------
# Reports router -- additional coverage
# ---------------------------------------------------------------------------

class TestReportListPublic:
    """GET /reports?scope=public returns publicly visible reports."""

    def test_list_public_empty(self, client, services):
        _seed(services)
        h = make_headers("user-1")
        resp = client.get("/reports?scope=public", headers=h)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_public_with_public_report(self, client, services):
        _seed(services)
        h = make_headers("user-1")
        rid = _create_report(client, h)
        client.put(f"/reports/{rid}", json={"visibility": "public_auth"}, headers=h)
        resp = client.get("/reports?scope=public", headers=h)
        assert resp.status_code == 200
        assert any(r["id"] == rid for r in resp.json())


class TestReportUpdate:
    """PUT /reports/:id -- partial updates."""

    def test_update_abstract_only(self, client, services):
        _seed(services)
        h = make_headers("user-1")
        rid = _create_report(client, h, title="Original")
        resp = client.put(f"/reports/{rid}", json={"abstract": "New abstract"}, headers=h)
        assert resp.status_code == 200
        assert resp.json()["abstract"] == "New abstract"

    def test_update_nonexistent_report_denied(self, client, services):
        _seed(services)
        h = make_headers("user-1")
        resp = client.put("/reports/nonexistent", json={"title": "X"}, headers=h)
        assert resp.status_code == 403


class TestSectionLocking:
    """POST/DELETE /reports/:id/sections/:sid/lock."""

    def test_acquire_and_release_lock(self, client, services):
        _seed(services)
        h = make_headers("user-1")
        rid = _create_report(client, h)
        sec = client.post(f"/reports/{rid}/sections", json={"content": "text"}, headers=h).json()
        sid = sec["id"]

        resp = client.post(f"/reports/{rid}/sections/{sid}/lock", headers=h)
        assert resp.status_code == 200
        assert resp.json()["acquired"] is True

        resp = client.delete(f"/reports/{rid}/sections/{sid}/lock", headers=h)
        assert resp.status_code == 204


class TestSectionVersions:
    """GET /reports/:id/sections/:sid/versions."""

    def test_list_versions(self, client, services):
        _seed(services)
        h = make_headers("user-1")
        rid = _create_report(client, h)
        sec = client.post(f"/reports/{rid}/sections", json={"content": "v1"}, headers=h).json()
        sid = sec["id"]
        # Edit to create a version
        client.put(f"/reports/{rid}/sections/{sid}", json={"content": "v2"}, headers=h)
        resp = client.get(f"/reports/{rid}/sections/{sid}/versions", headers=h)
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


# ---------------------------------------------------------------------------
# Sharing router
# ---------------------------------------------------------------------------

# client removed — the main `client` fixture (conftest.py) now
# handles dishka injection for all routers including sharing.


class TestSharingAccess:
    """Tests for /reports/:id/access endpoints."""

    def test_list_access_empty(self, client, services):
        _seed(services)
        h = make_headers("user-1")
        rid = _create_report(client, h)
        resp = client.get(f"/reports/{rid}/access", headers=h)
        assert resp.status_code == 200
        # The owner grant is set by report creation
        assert isinstance(resp.json(), list)

    def test_grant_user_access(self, client, services):
        _seed(services)
        _seed(services, "user-2")
        h = make_headers("user-1")
        rid = _create_report(client, h)
        resp = client.post(
            f"/reports/{rid}/access",
            json={"user_id": _stable_uuid("user-2"), "level": "editor"},
            headers=h,
        )
        assert resp.status_code == 201
        assert resp.json()["status"] == "ok"

        # Verify the grant shows up in list
        grants = client.get(f"/reports/{rid}/access", headers=h).json()
        user_ids = [g.get("user_id") for g in grants]
        assert _stable_uuid("user-2") in user_ids

    def test_grant_group_access(self, client, services):
        _seed(services)
        h = make_headers("user-1")
        rid = _create_report(client, h)
        # Create a group first
        grp = client.post("/groups", json={"name": "Team A"}, headers=h).json()
        gid = grp["id"]
        resp = client.post(
            f"/reports/{rid}/access",
            json={"group_id": gid, "level": "viewer"},
            headers=h,
        )
        assert resp.status_code == 201

    def test_revoke_access(self, client, services):
        _seed(services)
        _seed(services, "user-2")
        h = make_headers("user-1")
        rid = _create_report(client, h)
        client.post(
            f"/reports/{rid}/access",
            json={"user_id": _stable_uuid("user-2"), "level": "editor"},
            headers=h,
        )
        grants = client.get(f"/reports/{rid}/access", headers=h).json()
        user2_grant = [g for g in grants if g.get("user_id") == _stable_uuid("user-2")]
        assert len(user2_grant) == 1
        access_id = user2_grant[0]["id"]
        resp = client.delete(f"/reports/{rid}/access/{access_id}", headers=h)
        assert resp.status_code == 204

    def test_non_owner_cannot_list_access(self, client, services):
        _seed(services)
        _seed(services, "user-2")
        h_owner = make_headers("user-1")
        h_other = make_headers("user-2")
        rid = _create_report(client, h_owner)
        resp = client.get(f"/reports/{rid}/access", headers=h_other)
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Issues router
# ---------------------------------------------------------------------------

class TestIssuesList:
    """GET /issues -- list open issues and filter by entity."""

    def test_list_open_issues(self, client, services):
        _seed(services)
        h = make_headers("user-1")
        _create_issue(client, h)
        resp = client.get("/issues", headers=h)
        assert resp.status_code == 200
        assert len(resp.json()) >= 1

    def test_list_issues_by_entity(self, client, services):
        _seed(services)
        h = make_headers("user-1")
        _create_issue(client, h)
        resp = client.get("/issues?entity_type=company&entity_id=comp-1", headers=h)
        assert resp.status_code == 200
        assert len(resp.json()) >= 1

    def test_list_issues_empty_entity(self, client, services):
        _seed(services)
        h = make_headers("user-1")
        resp = client.get("/issues?entity_type=person&entity_id=nobody", headers=h)
        assert resp.status_code == 200
        assert resp.json() == []


class TestIssueGet:
    """GET /issues/:id."""

    def test_get_issue(self, client, services):
        _seed(services)
        h = make_headers("user-1")
        iid = _create_issue(client, h)
        resp = client.get(f"/issues/{iid}", headers=h)
        assert resp.status_code == 200
        assert resp.json()["id"] == iid
        assert resp.json()["title"] == "Bad data"

    def test_get_nonexistent_issue(self, client, services):
        _seed(services)
        resp = client.get("/issues/nonexistent", headers=make_headers("user-1"))
        assert resp.status_code == 404


class TestIssueComments:
    """POST /issues/:id/comments."""

    def test_add_comment(self, client, services):
        _seed(services)
        h = make_headers("user-1")
        iid = _create_issue(client, h)
        resp = client.post(
            f"/issues/{iid}/comments",
            json={"body": "I can confirm this."},
            headers=h,
        )
        assert resp.status_code == 201
        assert resp.json()["body_md"] == "I can confirm this."


class TestIssueVoting:
    """POST /issues/:id/vote."""

    def test_upvote(self, client, services):
        _seed(services)
        h = make_headers("user-1")
        iid = _create_issue(client, h)
        resp = client.post(
            f"/issues/{iid}/vote",
            json={"direction": "up"},
            headers=h,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_vote_nonexistent_issue(self, client, services):
        _seed(services)
        resp = client.post(
            "/issues/nonexistent/vote",
            json={"direction": "up"},
            headers=make_headers("user-1"),
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Groups router
# ---------------------------------------------------------------------------

class TestGroupCRUD:
    """POST /groups, GET /groups/:id, members."""

    def test_create_group(self, client, services):
        _seed(services)
        h = make_headers("user-1")
        resp = client.post("/groups", json={"name": "Analysts", "description": "Research team"}, headers=h)
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Analysts"
        assert data["id"] is not None

    def test_get_group(self, client, services):
        _seed(services)
        h = make_headers("user-1")
        gid = client.post("/groups", json={"name": "G"}, headers=h).json()["id"]
        resp = client.get(f"/groups/{gid}", headers=h)
        assert resp.status_code == 200
        assert resp.json()["name"] == "G"
        assert "members" in resp.json()

    def test_get_nonexistent_group(self, client, services):
        _seed(services)
        resp = client.get("/groups/nonexistent", headers=make_headers("user-1"))
        assert resp.status_code == 404

    def test_add_member(self, client, services):
        _seed(services)
        _seed(services, "user-2")
        h = make_headers("user-1")
        gid = client.post("/groups", json={"name": "G"}, headers=h).json()["id"]
        resp = client.post(f"/groups/{gid}/members", json={"user_id": _stable_uuid("user-2")}, headers=h)
        assert resp.status_code == 201
        assert resp.json()["status"] == "ok"

        # Verify member appears in group details
        group = client.get(f"/groups/{gid}", headers=h).json()
        assert _stable_uuid("user-2") in group["members"]

    def test_remove_member(self, client, services):
        _seed(services)
        _seed(services, "user-2")
        h = make_headers("user-1")
        gid = client.post("/groups", json={"name": "G"}, headers=h).json()["id"]
        client.post(f"/groups/{gid}/members", json={"user_id": _stable_uuid("user-2")}, headers=h)
        resp = client.delete(f"/groups/{gid}/members/{_stable_uuid('user-2')}", headers=h)
        assert resp.status_code == 204

    def test_add_member_to_nonexistent_group(self, client, services):
        _seed(services)
        resp = client.post(
            "/groups/nonexistent/members",
            json={"user_id": _stable_uuid("user-1")},
            headers=make_headers("user-1"),
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Users router
# ---------------------------------------------------------------------------

class TestGetOtherUser:
    """GET /users/:id -- fetch another user's public profile."""

    def test_get_other_user(self, client, services):
        _seed(services)
        _seed(services, "user-2")
        resp = client.get(f"/users/{_stable_uuid('user-2')}", headers=make_headers("user-1"))
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == _stable_uuid("user-2")
        assert "trust_level" in data
        # Private fields like email should not be present
        assert "email" not in data

    def test_get_nonexistent_user(self, client, services):
        _seed(services)
        resp = client.get("/users/ghost", headers=make_headers("user-1"))
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Moderation router
# ---------------------------------------------------------------------------

class TestModerationQueue:
    """GET /moderation/queue -- requires moderator."""

    def test_queue_as_moderator(self, client, services):
        _seed(services, "mod-1", trust_level="moderator")
        h = make_headers("mod-1")
        resp = client.get("/moderation/queue", headers=h)
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_queue_denied_for_regular_user(self, client, services):
        _seed(services)
        resp = client.get("/moderation/queue", headers=make_headers("user-1"))
        assert resp.status_code == 403


class TestModerationSanctions:
    """POST /moderation/sanctions -- create sanctions."""

    def test_create_warning_as_moderator(self, client, services):
        _seed(services, "mod-1", trust_level="moderator")
        _seed(services, "bad-user")
        h = make_headers("mod-1")
        resp = client.post(
            "/moderation/sanctions",
            json={"user_id": "bad-user", "type": "warning", "reason": "Spamming"},
            headers=h,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["user_id"] == "bad-user"
        assert data["type"] == "warning"

    def test_create_ban_requires_admin(self, client, services):
        _seed(services, "mod-1", trust_level="moderator")
        _seed(services, "bad-user")
        h = make_headers("mod-1")
        resp = client.post(
            "/moderation/sanctions",
            json={"user_id": "bad-user", "type": "ban", "reason": "Severe abuse"},
            headers=h,
        )
        # Moderator cannot ban, only admin can
        assert resp.status_code == 403

    def test_create_ban_as_admin(self, client, services):
        _seed(services, "admin-1", trust_level="admin", roles=["admin"])
        _seed(services, "bad-user")
        h = make_headers("admin-1")
        resp = client.post(
            "/moderation/sanctions",
            json={"user_id": "bad-user", "type": "ban", "reason": "Severe abuse"},
            headers=h,
        )
        assert resp.status_code == 201
        assert resp.json()["type"] == "ban"

    def test_sanction_denied_for_regular_user(self, client, services):
        _seed(services)
        resp = client.post(
            "/moderation/sanctions",
            json={"user_id": "someone", "type": "warning", "reason": "test"},
            headers=make_headers("user-1"),
        )
        assert resp.status_code == 403


class TestModerationLog:
    """GET /moderation/log -- requires moderator."""

    def test_log_as_moderator(self, client, services):
        _seed(services, "mod-1", trust_level="moderator")
        resp = client.get("/moderation/log", headers=make_headers("mod-1"))
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)
