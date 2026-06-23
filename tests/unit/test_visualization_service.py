"""VisualizationService — ownership, investigation attach via can_add_viz (M5)."""
from __future__ import annotations

import pytest

from src.services.exceptions import NotFound, PermissionDenied
from tests.conftest import _stable_uuid, seed_user


async def _users(services, *names):
    for n in names:
        await seed_user(services["user_repo"], n)
    return [_stable_uuid(n) for n in names]


@pytest.mark.asyncio
async def test_create_and_list_mine(services):
    (u1,) = await _users(services, "u1")
    vsvc = services["visualization_svc"]
    v = await vsvc.create(u1, "Snap", "chart_snapshot", {"entityId": "AAPL"})
    assert v.id and v.created_by == u1
    assert [x.id for x in await vsvc.list_mine(u1)] == [v.id]


@pytest.mark.asyncio
async def test_nonowner_cannot_read_or_delete(services):
    u1, u2 = await _users(services, "u1", "u2")
    vsvc = services["visualization_svc"]
    v = await vsvc.create(u1, "S", "map", {})
    with pytest.raises(PermissionDenied):
        await vsvc.get(u2, v.id)
    with pytest.raises(PermissionDenied):
        await vsvc.delete(u2, v.id)


@pytest.mark.asyncio
async def test_create_on_investigation_requires_add_viz(services):
    u1, u2, u3 = await _users(services, "u1", "u2", "u3")
    isvc, vsvc = services["investigation_svc"], services["visualization_svc"]
    inv = await isvc.create(u1, "Inv")
    await isvc.set_member(u1, inv.id, u2, can_add_viz=True)
    await isvc.set_member(u1, inv.id, u3)  # no caps
    # owner + add-viz member can save straight onto the investigation
    await vsvc.create(u1, "by owner", "map", {}, investigation_id=inv.id)
    await vsvc.create(u2, "by viz member", "map", {}, investigation_id=inv.id)
    with pytest.raises(PermissionDenied):
        await vsvc.create(u3, "by viewer", "map", {}, investigation_id=inv.id)
    assert len(await vsvc.list_for_investigation(u1, inv.id)) == 2


@pytest.mark.asyncio
async def test_attach_detach_and_list(services):
    (u1,) = await _users(services, "u1")
    isvc, vsvc = services["investigation_svc"], services["visualization_svc"]
    inv = await isvc.create(u1, "Inv")
    v = await vsvc.create(u1, "S", "map", {})
    await vsvc.attach(u1, v.id, inv.id)
    assert [x.id for x in await vsvc.list_for_investigation(u1, inv.id)] == [v.id]
    await vsvc.detach(u1, v.id)
    assert await vsvc.list_for_investigation(u1, inv.id) == []


@pytest.mark.asyncio
async def test_attach_requires_add_viz_on_target(services):
    u1, u2 = await _users(services, "u1", "u2")
    isvc, vsvc = services["investigation_svc"], services["visualization_svc"]
    inv_other = await isvc.create(u2, "Theirs")  # u1 is not a member
    v = await vsvc.create(u1, "S", "map", {})
    with pytest.raises(PermissionDenied):
        await vsvc.attach(u1, v.id, inv_other.id)


@pytest.mark.asyncio
async def test_list_for_investigation_requires_read(services):
    u1, u2 = await _users(services, "u1", "u2")
    isvc, vsvc = services["investigation_svc"], services["visualization_svc"]
    inv = await isvc.create(u1, "Inv")
    await vsvc.create(u1, "S", "map", {}, investigation_id=inv.id)
    with pytest.raises(PermissionDenied):
        await vsvc.list_for_investigation(u2, inv.id)


@pytest.mark.asyncio
async def test_unknown_404(services):
    (u1,) = await _users(services, "u1")
    with pytest.raises(NotFound):
        await services["visualization_svc"].get(u1, "00000000-0000-0000-0000-000000000000")


@pytest.mark.asyncio
async def test_investigation_delete_handles_viz(services):
    (u1,) = await _users(services, "u1")
    isvc, vsvc = services["investigation_svc"], services["visualization_svc"]
    vrepo = services["visualization_repo"]
    # orphan keeps the viz (link nulled)
    inv = await isvc.create(u1, "Inv")
    v = await vsvc.create(u1, "S", "map", {}, investigation_id=inv.id)
    await isvc.delete(u1, inv.id, content="orphan")
    kept = await vrepo.get_by_id(v.id)
    assert kept is not None and kept.investigation_id is None
    # cascade deletes the viz
    inv2 = await isvc.create(u1, "Inv2")
    v2 = await vsvc.create(u1, "S2", "map", {}, investigation_id=inv2.id)
    await isvc.delete(u1, inv2.id, content="cascade")
    assert await vrepo.get_by_id(v2.id) is None
