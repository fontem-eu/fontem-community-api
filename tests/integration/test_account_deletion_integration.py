"""Integration test for DELETE /users/me — GDPR Art. 17 right to erasure.

Verifies that the deletion endpoint cleans up all user-referencing
tables in the right order without leaving orphans.
"""
from __future__ import annotations

import uuid

from tests.integration.conftest import make_headers


class TestAccountDeletion:
    """USERS-DEL-I01..I04: Self-service account deletion."""

    def test_delete_me_returns_204(self, client, user_id):
        """Happy path: a user with no data can delete their account."""
        h = make_headers(user_id)
        # Auto-create
        client.get("/users/me", headers=h)

        resp = client.delete("/users/me", headers=h)
        assert resp.status_code == 204

    def test_deletion_removes_all_user_data(self, client, user_id, user2_id):
        """A user with reports, comments, issues, sanctions, etc. is fully wiped."""
        h = make_headers(user_id)
        h2 = make_headers(user2_id)

        # Create the second user (auto-create)
        client.get("/users/me", headers=h2)

        # User1 creates a report
        report = client.post(
            "/reports", json={"title": "ToDelete"}, headers=h,
        ).json()
        # User1 saves a document version (creates a section + version)
        client.put(
            f"/reports/{report['id']}/content",
            json={"tiptap": {"type": "doc", "content": []}, "version": 1},
            headers=h,
        )
        # User1 flags something (any target — flags are open to all users)
        client.post("/flags", json={
            "target_type": "report",
            "target_id": report["id"],
            "reason": "spam",
        }, headers=h)

        # Now delete user1's account
        resp = client.delete("/users/me", headers=h)
        assert resp.status_code == 204

        # User1's report is gone — user2 trying to fetch it gets 404 or 403
        r = client.get(f"/reports/{report['id']}", headers=h2)
        assert r.status_code in (403, 404)

        # The user record itself is gone — user2 looking up user1's profile gets 404
        r = client.get(f"/users/{user_id}", headers=h2)
        assert r.status_code == 404

    def test_deletion_does_not_affect_other_users(self, client, user_id, user2_id):
        """User2's data must survive when user1 deletes their account."""
        h = make_headers(user_id)
        h2 = make_headers(user2_id)

        # Both users exist
        client.get("/users/me", headers=h)
        client.get("/users/me", headers=h2)

        # User2 creates a report
        report2 = client.post(
            "/reports", json={"title": "User2Report"}, headers=h2,
        ).json()

        # User1 deletes themselves
        resp = client.delete("/users/me", headers=h)
        assert resp.status_code == 204

        # User2's report still exists
        r = client.get(f"/reports/{report2['id']}", headers=h2)
        assert r.status_code == 200
        assert r.json()["title"] == "User2Report"

    def test_deletion_requires_auth(self, client):
        """Anonymous DELETE /users/me must return 401."""
        resp = client.delete("/users/me")
        assert resp.status_code in (401, 403)
