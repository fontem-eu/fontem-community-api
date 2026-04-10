"""Dossier tree — nested reports via parent_id."""
from __future__ import annotations

import asyncio

from tests.conftest import make_headers, seed_user


class TestDossierTree:
    """Cover dossier (nested report) functionality."""

    def test_create_child_report(self, client, services):
        """Reports can be nested via parent_id."""
        asyncio.get_event_loop().run_until_complete(
            seed_user(services["user_repo"], "user-1")
        )
        h = make_headers("user-1")

        parent = client.post(
            "/reports", json={"title": "Investigation Dossier"}, headers=h,
        ).json()
        child = client.post(
            "/reports",
            json={"title": "Chapter 1", "parent_id": parent["id"]},
            headers=h,
        ).json()
        assert child["parent_id"] == parent["id"]

    def test_get_report_includes_children(self, client, services):
        """GET /reports/:id includes children list."""
        asyncio.get_event_loop().run_until_complete(
            seed_user(services["user_repo"], "user-1")
        )
        h = make_headers("user-1")

        parent = client.post(
            "/reports", json={"title": "Dossier"}, headers=h,
        ).json()
        client.post(
            "/reports",
            json={"title": "Page A", "parent_id": parent["id"]},
            headers=h,
        )
        client.post(
            "/reports",
            json={"title": "Page B", "parent_id": parent["id"]},
            headers=h,
        )

        result = client.get(f"/reports/{parent['id']}", headers=h).json()
        assert len(result["children"]) == 2
        titles = [c["title"] for c in result["children"]]
        assert "Page A" in titles
        assert "Page B" in titles

    def test_root_report_has_no_parent(self, client, services):
        """Root-level reports have parent_id = None."""
        asyncio.get_event_loop().run_until_complete(
            seed_user(services["user_repo"], "user-1")
        )
        h = make_headers("user-1")
        report = client.post(
            "/reports", json={"title": "Root Report"}, headers=h,
        ).json()
        assert report.get("parent_id") is None
