"""Tests for the LLM assist endpoint."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

from tests.conftest import make_headers, seed_user


class TestAssistAPI:
    """Cover /assist endpoints."""

    def test_chat_returns_response(self, client, services):
        """POST /assist/chat returns a 200 with content."""
        import asyncio
        asyncio.get_event_loop().run_until_complete(
            seed_user(services["user_repo"], "user-1")
        )
        resp = client.post(
            "/assist/chat",
            json={"message": "Hello"},
            headers=make_headers("user-1"),
        )
        assert resp.status_code == 200
        assert "content" in resp.json()

    def test_chat_requires_auth(self, client):
        """POST /assist/chat without auth returns 401/403."""
        resp = client.post("/assist/chat", json={"message": "Hello"})
        assert resp.status_code in (401, 403)

    def test_list_tools(self, client, services):
        """GET /assist/tools returns available tools."""
        import asyncio
        asyncio.get_event_loop().run_until_complete(
            seed_user(services["user_repo"], "user-1")
        )
        resp = client.get("/assist/tools", headers=make_headers("user-1"))
        assert resp.status_code == 200
        tools = resp.json()
        assert len(tools) >= 5
        names = [t["name"] for t in tools]
        assert "search_entities" in names
        assert "explore_graph" in names
        assert "suggest_visualization" in names


class TestDossierTree:
    """Cover dossier (nested report) functionality."""

    def test_create_child_report(self, client, services):
        """Reports can be nested via parent_id."""
        import asyncio
        asyncio.get_event_loop().run_until_complete(
            seed_user(services["user_repo"], "user-1")
        )
        h = make_headers("user-1")

        # Create parent dossier
        parent = client.post(
            "/reports", json={"title": "Investigation Dossier"}, headers=h,
        ).json()

        # Create child page
        child = client.post(
            "/reports",
            json={"title": "Chapter 1", "parent_id": parent["id"]},
            headers=h,
        ).json()

        assert child["parent_id"] == parent["id"]

    def test_get_report_includes_children(self, client, services):
        """GET /reports/:id includes children list."""
        import asyncio
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
        import asyncio
        asyncio.get_event_loop().run_until_complete(
            seed_user(services["user_repo"], "user-1")
        )
        h = make_headers("user-1")
        report = client.post(
            "/reports", json={"title": "Root Report"}, headers=h,
        ).json()
        assert report.get("parent_id") is None
