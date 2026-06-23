"""Investigations M4 — article association + delete cascade/orphan (service)."""
from __future__ import annotations

import pytest

from src.services.exceptions import NotFound, PermissionDenied
from tests.conftest import _stable_uuid, seed_user


async def _users(services, *names):
    for n in names:
        await seed_user(services["user_repo"], n)
    return [_stable_uuid(n) for n in names]


@pytest.mark.asyncio
async def test_owner_can_add_and_list_story(services):
    (u1,) = await _users(services, "u1")
    isvc, rsvc = services["investigation_svc"], services["report_svc"]
    inv = await isvc.create(u1, "Inv")
    r = await rsvc.create(u1, "Story A")
    await isvc.add_story(u1, inv.id, r.id)
    stories = await isvc.list_stories(u1, inv.id)
    assert [s["id"] for s in stories] == [r.id]
    assert stories[0]["title"] == "Story A"


@pytest.mark.asyncio
async def test_add_story_requires_write_cap(services):
    u1, u2, u3 = await _users(services, "u1", "u2", "u3")
    isvc, rsvc = services["investigation_svc"], services["report_svc"]
    inv = await isvc.create(u1, "Inv")
    await isvc.set_member(u1, inv.id, u2, role="contributor")
    await isvc.set_member(u1, inv.id, u3)  # viewer, no caps
    ra = await rsvc.create(u2, "by u2")
    await isvc.add_story(u2, inv.id, ra.id)  # write-cap member: ok
    rb = await rsvc.create(u3, "by u3")
    with pytest.raises(PermissionDenied):
        await isvc.add_story(u3, inv.id, rb.id)


@pytest.mark.asyncio
async def test_remove_story_detaches(services):
    (u1,) = await _users(services, "u1")
    isvc, rsvc = services["investigation_svc"], services["report_svc"]
    inv = await isvc.create(u1, "Inv")
    r = await rsvc.create(u1, "S")
    await isvc.add_story(u1, inv.id, r.id)
    await isvc.remove_story(u1, inv.id, r.id)
    assert await isvc.list_stories(u1, inv.id) == []


@pytest.mark.asyncio
async def test_add_unknown_report_404(services):
    (u1,) = await _users(services, "u1")
    isvc = services["investigation_svc"]
    inv = await isvc.create(u1, "Inv")
    with pytest.raises(NotFound):
        await isvc.add_story(u1, inv.id, "00000000-0000-0000-0000-000000000000")


@pytest.mark.asyncio
async def test_delete_orphan_keeps_articles_and_dossiers(services):
    (u1,) = await _users(services, "u1")
    isvc, rsvc, dsvc = (
        services["investigation_svc"], services["report_svc"], services["dossier_svc"])
    rrepo, drepo = services["report_repo"], services["dossier_repo"]
    inv = await isvc.create(u1, "Inv")
    loose = await rsvc.create(u1, "loose")
    await isvc.add_story(u1, inv.id, loose.id)
    dossier = await dsvc.create(u1, "D", investigation_id=inv.id)
    art = await rsvc.create(u1, "in dossier")
    await dsvc.add_article(u1, dossier.id, art.id)

    await isvc.delete(u1, inv.id, content="orphan")

    lr = await rrepo.get_by_id(loose.id)
    assert lr is not None and lr.investigation_id is None
    dd = await drepo.get_by_id(dossier.id)
    assert dd is not None and dd.investigation_id is None
    assert await rrepo.get_by_id(art.id) is not None  # dossier article survives


@pytest.mark.asyncio
async def test_delete_cascade_removes_articles_and_dossiers(services):
    (u1,) = await _users(services, "u1")
    isvc, rsvc, dsvc = (
        services["investigation_svc"], services["report_svc"], services["dossier_svc"])
    rrepo, drepo = services["report_repo"], services["dossier_repo"]
    inv = await isvc.create(u1, "Inv")
    loose = await rsvc.create(u1, "loose")
    await isvc.add_story(u1, inv.id, loose.id)
    dossier = await dsvc.create(u1, "D", investigation_id=inv.id)
    art = await rsvc.create(u1, "in dossier")
    await dsvc.add_article(u1, dossier.id, art.id)

    await isvc.delete(u1, inv.id, content="cascade")

    assert await rrepo.get_by_id(loose.id) is None
    assert await drepo.get_by_id(dossier.id) is None
    assert await rrepo.get_by_id(art.id) is None
