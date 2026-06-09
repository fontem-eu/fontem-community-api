"""
Integration tests for Reports — full HTTP API against real PostgreSQL.

Tests the complete lifecycle: create, read, update, delete, sections,
dossier nesting, permissions, and persistence across requests.
"""
from __future__ import annotations

from tests.integration.conftest import make_headers


class TestReportCRUD:
    """RPT-I01..I05: Basic report CRUD."""

    def test_create_report(self, client, user_id):
        """RPT-I01: Create report returns 201 with valid UUID id."""
        h = make_headers(user_id)
        resp = client.post("/reports", json={"title": "Test Report", "abstract": "Testing"}, headers=h)
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "Test Report"
        assert data["abstract"] == "Testing"
        assert len(data["id"]) == 36  # UUID format
        assert data["created_by"] == user_id

    def test_list_reports(self, client, user_id):
        """RPT-I02: List reports returns user's reports."""
        h = make_headers(user_id)
        client.post("/reports", json={"title": "R1"}, headers=h)
        client.post("/reports", json={"title": "R2"}, headers=h)
        resp = client.get("/reports", headers=h)
        assert resp.status_code == 200
        titles = [r["title"] for r in resp.json()]
        assert "R1" in titles
        assert "R2" in titles

    def test_get_report_with_sections(self, client, user_id):
        """RPT-I03: Get report by ID includes sections array."""
        h = make_headers(user_id)
        report = client.post("/reports", json={"title": "WithSections"}, headers=h).json()
        rid = report["id"]
        client.post(f"/reports/{rid}/sections", json={"content": "<p>Hello</p>"}, headers=h)
        resp = client.get(f"/reports/{rid}", headers=h)
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "WithSections"
        assert len(data["sections"]) == 1
        assert data["sections"][0]["content"] == "<p>Hello</p>"

    def test_update_report(self, client, user_id):
        """RPT-I04: Update title, abstract, visibility."""
        h = make_headers(user_id)
        report = client.post("/reports", json={"title": "Old"}, headers=h).json()
        rid = report["id"]
        resp = client.put(f"/reports/{rid}", json={"title": "New", "visibility": "public_open"}, headers=h)
        assert resp.status_code == 200
        assert resp.json()["title"] == "New"
        assert resp.json()["visibility"] == "public_open"
        # Verify persistence
        fetched = client.get(f"/reports/{rid}", headers=h).json()
        assert fetched["title"] == "New"

    def test_delete_report(self, client, user_id):
        """RPT-I05: Delete returns 204 and report disappears from list."""
        h = make_headers(user_id)
        report = client.post("/reports", json={"title": "ToDelete"}, headers=h).json()
        rid = report["id"]
        resp = client.delete(f"/reports/{rid}", headers=h)
        assert resp.status_code == 204
        # Should no longer appear in list
        reports = client.get("/reports", headers=h).json()
        assert not any(r["id"] == rid for r in reports)


class TestDossierTree:
    """RPT-I06..I07: Nested reports (dossier structure)."""

    def test_create_child_report(self, client, user_id):
        """RPT-I06: Create report with parent_id."""
        h = make_headers(user_id)
        parent = client.post("/reports", json={"title": "Dossier"}, headers=h).json()
        child = client.post("/reports", json={"title": "Chapter 1", "parent_id": parent["id"]}, headers=h).json()
        assert child["parent_id"] == parent["id"]

    def test_parent_includes_children(self, client, user_id):
        """RPT-I07: GET parent returns children list."""
        h = make_headers(user_id)
        parent = client.post("/reports", json={"title": "Dossier"}, headers=h).json()
        r1 = client.post("/reports", json={"title": "Ch1", "parent_id": parent["id"]}, headers=h)
        r2 = client.post("/reports", json={"title": "Ch2", "parent_id": parent["id"]}, headers=h)
        assert r1.status_code == 201, f"Child 1 failed: {r1.status_code} {r1.text[:100]}"
        assert r2.status_code == 201, f"Child 2 failed: {r2.status_code} {r2.text[:100]}"
        resp = client.get(f"/reports/{parent['id']}", headers=h)
        assert resp.status_code == 200, f"Get parent failed: {resp.status_code} {resp.text[:100]}"
        data = resp.json()
        assert len(data["children"]) == 2
        assert {c["title"] for c in data["children"]} == {"Ch1", "Ch2"}


class TestReportPersistence:
    """RPT-I08: Data survives across requests (no in-memory loss)."""

    def test_report_persists_across_requests(self, client, user_id):
        """RPT-I08: Report created in one request is readable in another."""
        h = make_headers(user_id)
        report = client.post("/reports", json={"title": "Persistent"}, headers=h).json()
        rid = report["id"]
        # Use a completely new request
        fetched = client.get(f"/reports/{rid}", headers=h).json()
        assert fetched["title"] == "Persistent"
        assert fetched["id"] == rid


class TestReportPermissions:
    """RPT-I09..I10: Access control."""

    def test_private_report_denied_to_other_user(self, client, user_id, user2_id):
        """RPT-I09: Other user cannot access private report."""
        h1 = make_headers(user_id)
        h2 = make_headers(user2_id)
        # Ensure both users exist in DB
        client.get("/users/me", headers=h1)
        client.get("/users/me", headers=h2)
        report = client.post("/reports", json={"title": "Private"}, headers=h1).json()
        resp = client.get(f"/reports/{report['id']}", headers=h2)
        assert resp.status_code == 403

    def test_public_report_visible_to_others(self, client, user_id, user2_id):
        """RPT-I10: Public report accessible by other users."""
        h1 = make_headers(user_id)
        h2 = make_headers(user2_id)
        # Ensure both users exist in DB
        client.get("/users/me", headers=h1)
        client.get("/users/me", headers=h2)
        report = client.post("/reports", json={"title": "Public Report"}, headers=h1).json()
        update_resp = client.put(
            f"/reports/{report['id']}",
            json={"visibility": "public_open"},
            headers=h1,
        )
        assert update_resp.status_code == 200, f"visibility update failed: {update_resp.text}"
        check = client.get(f"/reports/{report['id']}", headers=h1).json()
        assert check["visibility"] == "public_open", f"got {check['visibility']}"
        resp = client.get(f"/reports/{report['id']}", headers=h2)
        assert resp.status_code == 200
        assert resp.json()["title"] == "Public Report"
