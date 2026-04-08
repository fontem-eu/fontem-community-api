"""
Integration tests for Sharing/Access — full HTTP API against real PostgreSQL.
"""
from __future__ import annotations

import pytest
from tests.integration.conftest import make_headers


class TestSharing:
    """SHR-I01..I04: Access control and sharing."""

    def test_grant_access(self, client, user_id, user2_id):
        """SHR-I01: Grant user access to report."""
        h1 = make_headers(user_id)
        h2 = make_headers(user2_id)
        # Ensure user2 exists in the database before granting access
        client.get("/users/me", headers=h2)
        report = client.post("/reports", json={"title": "Shared"}, headers=h1).json()
        resp = client.post(
            f"/reports/{report['id']}/access",
            json={"user_id": user2_id, "level": "viewer"},
            headers=h1,
        )
        assert resp.status_code in (200, 201)

    def test_viewer_can_read(self, client, user_id, user2_id):
        """SHR-I02: Viewer can read shared report."""
        h1 = make_headers(user_id)
        h2 = make_headers(user2_id)
        # Auto-create user2
        client.get("/users/me", headers=h2)
        report = client.post("/reports", json={"title": "ForViewer"}, headers=h1).json()
        client.post(f"/reports/{report['id']}/access", json={"user_id": user2_id, "level": "viewer"}, headers=h1)
        resp = client.get(f"/reports/{report['id']}", headers=h2)
        assert resp.status_code == 200

    def test_viewer_cannot_edit(self, client, user_id, user2_id):
        """SHR-I03: Viewer cannot edit the report."""
        h1 = make_headers(user_id)
        h2 = make_headers(user2_id)
        client.get("/users/me", headers=h2)
        report = client.post("/reports", json={"title": "ReadOnly"}, headers=h1).json()
        client.post(f"/reports/{report['id']}/access", json={"user_id": user2_id, "level": "viewer"}, headers=h1)
        resp = client.put(f"/reports/{report['id']}", json={"title": "Hacked"}, headers=h2)
        assert resp.status_code == 403

    def test_revoke_access(self, client, user_id, user2_id):
        """SHR-I04: Revoking access removes permissions."""
        h1 = make_headers(user_id)
        h2 = make_headers(user2_id)
        client.get("/users/me", headers=h2)
        report = client.post("/reports", json={"title": "Revokable"}, headers=h1).json()
        client.post(f"/reports/{report['id']}/access", json={"user_id": user2_id, "level": "viewer"}, headers=h1)
        # Can read
        assert client.get(f"/reports/{report['id']}", headers=h2).status_code == 200
        # Get access list and revoke
        access_list = client.get(f"/reports/{report['id']}/access", headers=h1).json()
        viewer_grant = next((a for a in access_list if a.get("user_id") == user2_id), None)
        if viewer_grant:
            client.delete(f"/reports/{report['id']}/access/{viewer_grant['id']}", headers=h1)
        # Can no longer read
        resp = client.get(f"/reports/{report['id']}", headers=h2)
        assert resp.status_code == 403
