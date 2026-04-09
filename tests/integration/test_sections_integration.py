"""
Integration tests for Sections — full HTTP API against real PostgreSQL.
"""
from __future__ import annotations

import pytest
from tests.integration.conftest import make_headers


class TestSections:
    """SEC-I01..I06: Section lifecycle."""

    def _create_report(self, client, user_id):
        h = make_headers(user_id)
        return client.post("/reports", json={"title": "SectionTest"}, headers=h).json()["id"], h

    def test_add_section(self, client, user_id):
        """SEC-I01: Add section to report."""
        rid, h = self._create_report(client, user_id)
        resp = client.post(f"/reports/{rid}/sections", json={"content": "<p>Hello</p>"}, headers=h)
        assert resp.status_code == 201
        assert resp.json()["content"] == "<p>Hello</p>"

    def test_section_persists_on_reload(self, client, user_id):
        """SEC-I02: Section content persists when fetching report again."""
        rid, h = self._create_report(client, user_id)
        client.post(f"/reports/{rid}/sections", json={"content": "<p>Persisted</p>"}, headers=h)
        # Fetch report in new request
        report = client.get(f"/reports/{rid}", headers=h).json()
        assert len(report["sections"]) == 1
        assert report["sections"][0]["content"] == "<p>Persisted</p>"

    def test_update_section(self, client, user_id):
        """SEC-I03: Update section content."""
        rid, h = self._create_report(client, user_id)
        sec = client.post(f"/reports/{rid}/sections", json={"content": "<p>v1</p>"}, headers=h).json()
        resp = client.put(f"/reports/{rid}/sections/{sec['id']}", json={"content": "<p>v2</p>"}, headers=h)
        assert resp.status_code == 200
        assert resp.json()["content"] == "<p>v2</p>"

    def test_delete_section(self, client, user_id):
        """SEC-I04: Delete section."""
        rid, h = self._create_report(client, user_id)
        sec = client.post(f"/reports/{rid}/sections", json={"content": "<p>gone</p>"}, headers=h).json()
        resp = client.delete(f"/reports/{rid}/sections/{sec['id']}", headers=h)
        assert resp.status_code == 204
        # Verify removed
        report = client.get(f"/reports/{rid}", headers=h).json()
        assert len(report["sections"]) == 0

    def test_lock_prevents_concurrent_edit(self, client, user_id, user2_id):
        """SEC-I05: Locked section rejects edit from other user."""
        rid, h1 = self._create_report(client, user_id)
        # Ensure user2 exists in DB (auto-created by making any authenticated request)
        h2 = make_headers(user2_id)
        client.get("/users/me", headers=h2)
        # Grant user2 editor access
        grant = client.post(
            f"/reports/{rid}/access",
            json={"user_id": user2_id, "level": "editor"},
            headers=h1,
        )
        assert grant.status_code in (200, 201), f"grant failed: {grant.text}"
        # Create section
        sec = client.post(
            f"/reports/{rid}/sections",
            json={"content": "<p>locked</p>"},
            headers=h1,
        ).json()
        # Lock and verify it took
        lock_resp = client.post(f"/reports/{rid}/sections/{sec['id']}/lock", headers=h1)
        assert lock_resp.status_code in (200, 201), f"lock failed: {lock_resp.text}"
        # User2 tries to edit — should fail
        resp = client.put(
            f"/reports/{rid}/sections/{sec['id']}",
            json={"content": "<p>hacked</p>"},
            headers=h2,
        )
        assert resp.status_code in (409, 403)

    def test_section_versions_tracked(self, client, user_id):
        """SEC-I06: Editing creates version history."""
        rid, h = self._create_report(client, user_id)
        sec = client.post(f"/reports/{rid}/sections", json={"content": "<p>v1</p>"}, headers=h).json()
        client.put(f"/reports/{rid}/sections/{sec['id']}", json={"content": "<p>v2</p>"}, headers=h)
        resp = client.get(f"/reports/{rid}/sections/{sec['id']}/versions", headers=h)
        assert resp.status_code == 200
        versions = resp.json()
        assert len(versions) >= 1
