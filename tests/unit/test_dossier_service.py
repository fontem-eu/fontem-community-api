"""DossierService — CRUD, tree ops, cascade/orphan delete (M3)."""
from __future__ import annotations

import pytest

from src.services.exceptions import NotFound, PermissionDenied
from tests.conftest import _stable_uuid, seed_user


async def _u(services, *names):
    for n in names:
        await seed_user(services["user_repo"], n)
    return [_stable_uuid(n) for n in names]


@pytest.mark.asyncio
async def test_create_and_get(services):
    (u1,) = await _u(services, "u1")
    svc = services["dossier_svc"]
    d = await svc.create(u1, "Files")
    assert d.id and d.created_by == u1
    assert (await svc.get(u1, d.id)).name == "Files"


@pytest.mark.asyncio
async def test_nonowner_cannot_read(services):
    u1, u2 = await _u(services, "u1", "u2")
    svc = services["dossier_svc"]
    d = await svc.create(u1, "F")
    with pytest.raises(PermissionDenied):
        await svc.get(u2, d.id)


@pytest.mark.asyncio
async def test_add_articles_builds_tree(services):
    (u1,) = await _u(services, "u1")
    dsvc, rsvc = services["dossier_svc"], services["report_svc"]
    d = await dsvc.create(u1, "F")
    r1 = await rsvc.create(u1, "Root")
    r2 = await rsvc.create(u1, "Child")
    await dsvc.add_article(u1, d.id, r1.id)
    await dsvc.add_article(u1, d.id, r2.id, parent_id=r1.id)
    tree = await dsvc.tree(u1, d.id)
    assert len(tree) == 2
    child = next(n for n in tree if n["id"] == r2.id)
    assert child["parent_id"] == r1.id


@pytest.mark.asyncio
async def test_delete_orphan_keeps_articles(services):
    (u1,) = await _u(services, "u1")
    dsvc, rsvc, rrepo = services["dossier_svc"], services["report_svc"], services["report_repo"]
    d = await dsvc.create(u1, "F")
    r = await rsvc.create(u1, "A")
    await dsvc.add_article(u1, d.id, r.id)
    await dsvc.delete(u1, d.id, content="orphan")
    art = await rrepo.get_by_id(r.id)
    assert art is not None and art.dossier_id is None


@pytest.mark.asyncio
async def test_delete_cascade_removes_articles(services):
    (u1,) = await _u(services, "u1")
    dsvc, rsvc, rrepo = services["dossier_svc"], services["report_svc"], services["report_repo"]
    d = await dsvc.create(u1, "F")
    r = await rsvc.create(u1, "A")
    await dsvc.add_article(u1, d.id, r.id)
    await dsvc.delete(u1, d.id, content="cascade")
    assert await rrepo.get_by_id(r.id) is None


@pytest.mark.asyncio
async def test_remove_article_and_update(services):
    (u1,) = await _u(services, "u1")
    dsvc, rsvc = services["dossier_svc"], services["report_svc"]
    d = await dsvc.create(u1, "F")
    r = await rsvc.create(u1, "A")
    await dsvc.add_article(u1, d.id, r.id)
    await dsvc.remove_article(u1, d.id, r.id)
    assert await dsvc.tree(u1, d.id) == []
    assert (await dsvc.update(u1, d.id, "Renamed")).name == "Renamed"


@pytest.mark.asyncio
async def test_unknown_404(services):
    (u1,) = await _u(services, "u1")
    with pytest.raises(NotFound):
        await services["dossier_svc"].get(u1, "00000000-0000-0000-0000-000000000000")
