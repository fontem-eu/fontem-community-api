"""HTTP-level tests for report endpoints (covers routers/reports.py)."""
from __future__ import annotations

import asyncio

import pytest
from tests.conftest import make_headers, seed_user


@pytest.mark.asyncio
class TestReportAPI:
    """Cover report CRUD via the HTTP API."""

    async def _setup_user(self, services):
        await seed_user(services["user_repo"], "user-1")

    def test_create_report(self, client, services):
        """POST /reports creates a report and returns 201."""
        asyncio.get_event_loop().run_until_complete(self._setup_user(services))
        resp = client.post(
            "/reports",
            json={"title": "Test Report", "abstract": "An abstract"},
            headers=make_headers("user-1"),
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "Test Report"
        assert data["abstract"] == "An abstract"
        assert data["id"] is not None

    def test_list_reports(self, client, services):
        """GET /reports returns user's reports."""
        asyncio.get_event_loop().run_until_complete(self._setup_user(services))
        h = make_headers("user-1")
        client.post("/reports", json={"title": "R1"}, headers=h)
        client.post("/reports", json={"title": "R2"}, headers=h)
        resp = client.get("/reports", headers=h)
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_get_report(self, client, services):
        """GET /reports/:id returns report with sections."""
        asyncio.get_event_loop().run_until_complete(self._setup_user(services))
        h = make_headers("user-1")
        create = client.post("/reports", json={"title": "R"}, headers=h)
        rid = create.json()["id"]
        resp = client.get(f"/reports/{rid}", headers=h)
        assert resp.status_code == 200
        assert resp.json()["title"] == "R"
        assert "sections" in resp.json()

    def test_update_report(self, client, services):
        """PUT /reports/:id updates title and visibility."""
        asyncio.get_event_loop().run_until_complete(self._setup_user(services))
        h = make_headers("user-1")
        create = client.post("/reports", json={"title": "Old"}, headers=h)
        rid = create.json()["id"]
        resp = client.put(
            f"/reports/{rid}",
            json={"title": "New", "visibility": "public_open"},
            headers=h,
        )
        assert resp.status_code == 200
        assert resp.json()["title"] == "New"

    def test_update_report_nuts_region(self, client, services):
        """PUT sets the region tag; GET returns it; a bad code is 422."""
        asyncio.get_event_loop().run_until_complete(self._setup_user(services))
        h = make_headers("user-1")
        rid = client.post("/reports", json={"title": "R"}, headers=h).json()["id"]
        resp = client.put(f"/reports/{rid}", json={"nuts_region": "PT17"}, headers=h)
        assert resp.status_code == 200 and resp.json()["nuts_region"] == "PT17"
        got = client.get(f"/data-stories/{rid}", headers=h).json()
        assert got["nuts_region"] == "PT17"
        bad = client.put(f"/reports/{rid}", json={"nuts_region": "not-a-code"}, headers=h)
        assert bad.status_code == 422

    def test_delete_report(self, client, services):
        """DELETE /reports/:id returns 204."""
        asyncio.get_event_loop().run_until_complete(self._setup_user(services))
        h = make_headers("user-1")
        create = client.post("/reports", json={"title": "Doomed"}, headers=h)
        rid = create.json()["id"]
        resp = client.delete(f"/reports/{rid}", headers=h)
        assert resp.status_code == 204

    def test_add_section(self, client, services):
        """POST /reports/:id/sections creates a section."""
        asyncio.get_event_loop().run_until_complete(self._setup_user(services))
        h = make_headers("user-1")
        create = client.post("/reports", json={"title": "R"}, headers=h)
        rid = create.json()["id"]
        resp = client.post(
            f"/reports/{rid}/sections",
            json={"content": "<p>Hello</p>"},
            headers=h,
        )
        assert resp.status_code == 201
        assert resp.json()["content"] == "<p>Hello</p>"

    def test_section_persists_on_reload(self, client, services):
        """Section content is returned when fetching the report."""
        asyncio.get_event_loop().run_until_complete(self._setup_user(services))
        h = make_headers("user-1")
        create = client.post("/reports", json={"title": "R"}, headers=h)
        rid = create.json()["id"]
        client.post(
            f"/reports/{rid}/sections",
            json={"content": "<p>Persistent</p>"},
            headers=h,
        )
        resp = client.get(f"/reports/{rid}", headers=h)
        sections = resp.json()["sections"]
        assert len(sections) == 1
        assert sections[0]["content"] == "<p>Persistent</p>"

    def test_update_section(self, client, services):
        """PUT /reports/:id/sections/:sid updates content."""
        asyncio.get_event_loop().run_until_complete(self._setup_user(services))
        h = make_headers("user-1")
        create = client.post("/reports", json={"title": "R"}, headers=h)
        rid = create.json()["id"]
        sec = client.post(
            f"/reports/{rid}/sections",
            json={"content": "<p>v1</p>"},
            headers=h,
        ).json()
        resp = client.put(
            f"/reports/{rid}/sections/{sec['id']}",
            json={"content": "<p>v2</p>"},
            headers=h,
        )
        assert resp.status_code == 200
        assert resp.json()["content"] == "<p>v2</p>"

    def test_delete_section(self, client, services):
        """DELETE /reports/:id/sections/:sid returns 204."""
        asyncio.get_event_loop().run_until_complete(self._setup_user(services))
        h = make_headers("user-1")
        create = client.post("/reports", json={"title": "R"}, headers=h)
        rid = create.json()["id"]
        sec = client.post(
            f"/reports/{rid}/sections",
            json={"content": "<p>gone</p>"},
            headers=h,
        ).json()
        resp = client.delete(f"/reports/{rid}/sections/{sec['id']}", headers=h)
        assert resp.status_code == 204

    def test_get_nonexistent_report_returns_404(self, client, services):
        """GET /reports/00000000-0000-4000-8000-000000000000 returns 404.

        The old handler ran the perm check first and surfaced 403 on
        missing reports to avoid leaking existence. get_viewable now
        loads the report first and 404s if it's missing — correct for
        any caller since a nonexistent id tells you nothing either way.
        """
        asyncio.get_event_loop().run_until_complete(self._setup_user(services))
        resp = client.get("/reports/00000000-0000-4000-8000-000000000000", headers=make_headers("user-1"))
        assert resp.status_code == 404

    def test_canonical_data_stories_path(self, client, services):
        """The canonical /data-stories/* path mirrors the legacy /reports
        alias. Cover create + read end-to-end on the new prefix so the
        rename window doesn't silently break the new path.
        """
        asyncio.get_event_loop().run_until_complete(self._setup_user(services))
        h = make_headers("user-1")
        create = client.post(
            "/data-stories",
            json={"title": "Canonical path", "abstract": "via /data-stories"},
            headers=h,
        )
        assert create.status_code == 201
        sid = create.json()["id"]

        resp = client.get(f"/data-stories/{sid}", headers=h)
        assert resp.status_code == 200
        assert resp.json()["title"] == "Canonical path"

        # Story created via the canonical path is also readable via the
        # legacy alias, and vice-versa — same handlers, same DB row.
        legacy = client.get(f"/reports/{sid}", headers=h)
        assert legacy.status_code == 200
        assert legacy.json()["id"] == sid


@pytest.mark.asyncio
class TestReportPresignedUrls:
    """SEC-2026-06-11 #4 — bucket is private; reads come through
    presigned URLs minted by the router on every response.

    The test stub MinioStorage emits
    ``https://test-presigned/<key>?sig=stub`` so we can assert the
    rewrite happened without depending on a real MinIO. The end-to-end
    "the browser can fetch through nginx" leg is covered by the
    staging smoke suite (STORY-UPLOAD-SEC-1..2).
    """

    async def _seed_owner(self, services):
        await seed_user(services["user_repo"], "owner-1")

    def test_get_report_rewrites_uploads_to_presigned_url(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._seed_owner(services))
        h = make_headers("owner-1")
        rid = client.post("/reports", json={"title": "WithImage"}, headers=h).json()["id"]
        # Plant a stored doc that mimics a real TipTap v1 section with
        # an embedded /uploads/ image src — same shape the editor saves.
        key = "0319fb3d-987c-4fc4-8d64-044a4daca389/deadbeef.png"
        client.post(
            f"/reports/{rid}/sections",
            json={"content": f'<p><img src="/uploads/{key}"/></p>'},
            headers=h,
        )

        body = client.get(f"/reports/{rid}", headers=h).json()
        # The section content should now carry the presigned URL, not
        # the bare /uploads/ path.
        section_html = body["sections"][0]["content"]
        assert f"https://test-presigned/{key}?sig=stub" in section_html, section_html
        assert "/uploads/" not in section_html, section_html

    def test_anonymous_public_open_also_gets_presigned(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._seed_owner(services))
        h = make_headers("owner-1")
        rid = client.post("/reports", json={"title": "Pub"}, headers=h).json()["id"]
        client.put(
            f"/reports/{rid}", json={"visibility": "public_open"}, headers=h,
        )
        key = "0319fb3d-987c-4fc4-8d64-044a4daca389/cafebabe.jpg"
        client.post(
            f"/reports/{rid}/sections",
            json={"content": f'<img src="/uploads/{key}"/>'},
            headers=h,
        )
        # No auth header — anonymous read of a public_open story.
        body = client.get(f"/reports/{rid}").json()
        section_html = body["sections"][0]["content"]
        assert f"https://test-presigned/{key}?sig=stub" in section_html
        assert "/uploads/" not in section_html
