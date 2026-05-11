"""HTTP-level tests for moderation endpoints."""
from __future__ import annotations

import asyncio
import pytest
from tests.conftest import _stable_uuid, make_headers, seed_user


@pytest.mark.asyncio
class TestModerationAPI:
    """Cover /flags and /moderation endpoints."""

    async def _setup(self, services):
        await seed_user(services["user_repo"], "user-1", trust_level="contributor")
        await seed_user(services["user_repo"], "mod-1", trust_level="moderator", roles=["moderator"])

    def test_flag_content(self, client, services):
        """POST /flags flags content."""
        asyncio.get_event_loop().run_until_complete(self._setup(services))
        resp = client.post(
            "/flags",
            json={
                "target_type": "report",
                "target_id": "00000000-0000-4000-8000-000000000001",
                "reason": "spam",
                "details": "Obvious spam",
            },
            headers=make_headers("user-1"),
        )
        assert resp.status_code in (200, 201)

    def test_get_moderation_log(self, client, services):
        """GET /moderation/log returns the log."""
        asyncio.get_event_loop().run_until_complete(self._setup(services))
        resp = client.get("/moderation/log", headers=make_headers("mod-1"))
        assert resp.status_code == 200

    def test_apply_sanction(self, client, services):
        """POST /moderation/sanctions applies a sanction."""
        asyncio.get_event_loop().run_until_complete(self._setup(services))
        resp = client.post(
            "/moderation/sanctions",
            json={
                "user_id": _stable_uuid("user-1"),
                "type": "warning",
                "reason": "test warning",
            },
            headers=make_headers("mod-1"),
        )
        assert resp.status_code in (200, 201)
