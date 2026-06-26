"""ActivityService: recording + listing a user's CUD events."""
from __future__ import annotations

import pytest

from src.infra.memory.mem_activity_repo import InMemoryActivityRepository
from src.services.activity_service import ActivityService


@pytest.mark.asyncio
class TestActivityService:
    async def test_record_and_list_newest_first(self):
        svc = ActivityService(InMemoryActivityRepository())
        await svc.record("u1", "story", "s1", "created", "First")
        await svc.record("u1", "dossier", "d1", "created", "Second")
        await svc.record("u2", "issue", "i1", "created", "Other user")
        rows = await svc.list_for_actor("u1")
        assert [r["entity_type"] for r in rows] == ["dossier", "story"]  # newest first
        assert rows[0]["summary"] == "Second"
        assert all(r["action"] == "created" for r in rows)

    async def test_record_is_best_effort(self):
        class _Boom(InMemoryActivityRepository):
            async def record(self, event):
                raise RuntimeError("db down")
        svc = ActivityService(_Boom())
        # must not raise — a failed activity write can't break the CUD op
        await svc.record("u1", "story", "s1", "created", "x")

    async def test_pagination(self):
        svc = ActivityService(InMemoryActivityRepository())
        for n in range(5):
            await svc.record("u1", "issue", f"i{n}", "created", str(n))
        page = await svc.list_for_actor("u1", limit=2, offset=1)
        assert len(page) == 2
