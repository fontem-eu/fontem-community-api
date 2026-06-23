"""InMemoryVisualizationRepository — basic persistence + queries (M5)."""
from __future__ import annotations

import pytest

from src.domain.visualization import Visualization
from src.infra.memory.mem_visualization_repo import InMemoryVisualizationRepository


@pytest.mark.asyncio
async def test_create_get_and_list():
    repo = InMemoryVisualizationRepository()
    v = await repo.create(Visualization(
        name="Contracts over time", widget_type="chart_snapshot",
        config={"entityId": "AAPL"}, created_by="u1",
    ))
    assert v.id and v.created_at is not None
    got = await repo.get_by_id(v.id)
    assert got.config == {"entityId": "AAPL"}
    assert [x.id for x in await repo.list_for_user("u1")] == [v.id]
    assert await repo.list_for_user("other") == []


@pytest.mark.asyncio
async def test_investigation_attach_and_query():
    repo = InMemoryVisualizationRepository()
    v = await repo.create(Visualization(widget_type="map", created_by="u1"))
    assert await repo.list_by_investigation("inv1") == []
    await repo.set_investigation(v.id, "inv1")
    assert [x.id for x in await repo.list_by_investigation("inv1")] == [v.id]
    await repo.set_investigation(v.id, None)
    assert await repo.list_by_investigation("inv1") == []
