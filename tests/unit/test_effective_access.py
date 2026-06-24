"""Phase D — 'who has access & why' for dossiers + viz."""
from __future__ import annotations

import pytest

from tests.conftest import _stable_uuid, seed_user


async def _users(services, *names):
    for n in names:
        await seed_user(services["user_repo"], n)
    return [_stable_uuid(n) for n in names]


@pytest.mark.asyncio
async def test_dossier_effective_access_lists_owner_inherited_direct(services):
    o, c, x = await _users(services, "o", "c", "x")
    isvc, dsvc = services["investigation_svc"], services["dossier_svc"]
    inv = await isvc.create(o, "Inv")
    await isvc.set_member(o, inv.id, c, role="contributor")
    d = await dsvc.create(o, "D", investigation_id=inv.id)
    await dsvc.share(o, d.id, x, level="viewer")  # direct grant to a non-member

    rows = await dsvc.effective_access(o, d.id)
    by_user = {r["user_id"]: r for r in rows}
    # owner of the dossier (creator) shows as owner; also an investigation owner
    assert by_user[o]["level"] == "owner"
    # contributor inherits editor level from the investigation
    assert by_user[c]["level"] == "editor"
    assert by_user[c]["source"] == "inherited:contributor"
    # x has a direct viewer grant
    assert by_user[x]["level"] == "viewer"
    assert by_user[x]["source"] == "direct"


@pytest.mark.asyncio
async def test_viz_effective_access(services):
    o, x = await _users(services, "o", "x")
    vsvc = services["visualization_svc"]
    v = await vsvc.create(o, "Chart", "map", {})
    await vsvc.share(o, v.id, x, level="editor")
    rows = await vsvc.effective_access(o, v.id)
    by_user = {r["user_id"]: r for r in rows}
    assert by_user[o]["source"] == "owner"
    assert by_user[x]["level"] == "editor" and by_user[x]["source"] == "direct"
