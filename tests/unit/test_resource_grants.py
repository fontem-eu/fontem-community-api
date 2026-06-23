"""Phase C — per-item direct grants (the additive override) on dossiers + viz."""
from __future__ import annotations

import pytest

from src.services.exceptions import PermissionDenied
from tests.conftest import _stable_uuid, seed_user


async def _users(services, *names):
    for n in names:
        await seed_user(services["user_repo"], n)
    return [_stable_uuid(n) for n in names]


@pytest.mark.asyncio
async def test_dossier_direct_grant_without_membership(services):
    o, x = await _users(services, "o", "x")
    dsvc, rsvc = services["dossier_svc"], services["report_svc"]
    d = await dsvc.create(o, "D")  # standalone, owned by o
    with pytest.raises(PermissionDenied):
        await dsvc.get(x, d.id)
    # viewer grant -> read, not edit
    await dsvc.share(o, d.id, x, level="viewer")
    assert (await dsvc.get(x, d.id)).id == d.id
    art = await rsvc.create(x, "A")
    with pytest.raises(PermissionDenied):
        await dsvc.add_article(x, d.id, art.id)
    # editor grant -> can edit (add article)
    await dsvc.share(o, d.id, x, level="editor")
    await dsvc.add_article(x, d.id, art.id)
    # revoke -> access gone
    await dsvc.revoke(o, d.id, x)
    with pytest.raises(PermissionDenied):
        await dsvc.get(x, d.id)


@pytest.mark.asyncio
async def test_only_owner_or_admin_can_share(services):
    o, c, a, x = await _users(services, "o", "c", "a", "x")
    isvc, dsvc = services["investigation_svc"], services["dossier_svc"]
    inv = await isvc.create(o, "Inv")
    await isvc.set_member(o, inv.id, c, role="contributor")
    await isvc.set_member(o, inv.id, a, role="admin")
    d = await dsvc.create(o, "D", investigation_id=inv.id)
    # contributor cannot share (needs admin or owner)
    with pytest.raises(PermissionDenied):
        await dsvc.share(c, d.id, x, level="viewer")
    # admin (role, not creator) can
    await dsvc.share(a, d.id, x, level="viewer")
    assert any(g["user_id"] == x for g in await dsvc.list_grants(o, d.id))


@pytest.mark.asyncio
async def test_viz_direct_grant(services):
    o, x = await _users(services, "o", "x")
    vsvc = services["visualization_svc"]
    v = await vsvc.create(o, "Chart", "map", {})
    with pytest.raises(PermissionDenied):
        await vsvc.get(x, v.id)
    await vsvc.share(o, v.id, x, level="viewer")
    assert (await vsvc.get(x, v.id)).id == v.id
    with pytest.raises(PermissionDenied):
        await vsvc.update(x, v.id, "nope")
    await vsvc.share(o, v.id, x, level="editor")
    await vsvc.update(x, v.id, "renamed")
    await vsvc.revoke(o, v.id, x)
    with pytest.raises(PermissionDenied):
        await vsvc.get(x, v.id)


@pytest.mark.asyncio
async def test_share_by_email(services):
    o, x = await _users(services, "o", "x")
    dsvc = services["dossier_svc"]
    d = await dsvc.create(o, "D")
    await dsvc.share(o, d.id, target_email="x@test.com", level="viewer")
    assert (await dsvc.get(x, d.id)).id == d.id
