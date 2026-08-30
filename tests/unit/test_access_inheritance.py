"""Phase B — investigation membership confers access to contained articles,
dossiers and viz, by role (inheritance + additive overrides). The access matrix.

Users: O = investigation owner + creator of the resources; C = contributor
member; V = viewer member; X = outsider; O2 = a second investigation owner who
is NOT the resource creator (so its access is pure inheritance).
"""
from __future__ import annotations

import pytest

from src.services.access_inheritance import max_level
from src.services.exceptions import PermissionDenied
from tests.conftest import _stable_uuid, seed_user


async def _users(services, *names):
    for n in names:
        await seed_user(services["user_repo"], n)
    return [_stable_uuid(n) for n in names]


async def _inv_with_members(services):
    o, c, v, x, o2 = await _users(services, "o", "c", "v", "x", "o2")
    isvc = services["investigation_svc"]
    inv = await isvc.create(o, "Inv")
    await isvc.set_member(o, inv.id, c, role="contributor")
    await isvc.set_member(o, inv.id, v, role="viewer")
    await isvc.set_member(o, inv.id, o2, role="owner")
    return inv, o, c, v, x, o2


# ── resolver units ──
def test_max_level():
    assert max_level(None, None) is None
    assert max_level("viewer", None) == "viewer"
    assert max_level("viewer", "editor") == "editor"
    assert max_level("owner", "editor") == "owner"


# ── ARTICLE inheritance ──
@pytest.mark.asyncio
async def test_article_read_inherited_by_every_member(services):
    inv, o, c, v, x, _ = await _inv_with_members(services)
    rsvc, isvc = services["report_svc"], services["investigation_svc"]
    art = await rsvc.create(o, "A")
    await isvc.add_story(o, inv.id, art.id)
    # viewer + contributor read it purely by inheritance (they didn't create it)
    assert (await rsvc.get_viewable(v, art.id)).id == art.id
    assert (await rsvc.get_viewable(c, art.id)).id == art.id
    # outsider cannot
    with pytest.raises(Exception):  # noqa: B017  (NotFound for anon-style hide)
        await rsvc.get_viewable(x, art.id)


@pytest.mark.asyncio
async def test_article_content_edit_needs_contributor(services):
    inv, o, c, v, x, _ = await _inv_with_members(services)
    rsvc, isvc = services["report_svc"], services["investigation_svc"]
    art = await rsvc.create(o, "A")
    await isvc.add_story(o, inv.id, art.id)
    # contributor edits content via inheritance (editor level)
    await rsvc.save_document(c, art.id, {"version": 2, "tiptap": {}}, None)
    # viewer cannot edit content
    with pytest.raises(PermissionDenied):
        await rsvc.save_document(v, art.id, {"version": 2, "tiptap": {}}, None)
    with pytest.raises(PermissionDenied):
        await rsvc.save_document(x, art.id, {"version": 2, "tiptap": {}}, None)


@pytest.mark.asyncio
async def test_article_meta_and_delete_need_owner(services):
    inv, o, c, v, _x, o2 = await _inv_with_members(services)
    rsvc, isvc = services["report_svc"], services["investigation_svc"]
    rrepo = services["report_repo"]
    art = await rsvc.create(o, "A")
    await isvc.add_story(o, inv.id, art.id)
    # contributor + viewer cannot rename (meta = owner level)
    with pytest.raises(PermissionDenied):
        await rsvc.update(c, art.id, title="hijack")
    with pytest.raises(PermissionDenied):
        await rsvc.update(v, art.id, title="hijack")
    # a SECOND investigation owner (not the creator) deletes it via inheritance
    await rsvc.delete(o2, art.id)
    assert await rrepo.get_by_id(art.id) is None


@pytest.mark.asyncio
async def test_article_inheritance_via_dossier(services):
    inv, o, c, _v, x, _o2 = await _inv_with_members(services)
    rsvc, dsvc = services["report_svc"], services["dossier_svc"]
    dossier = await dsvc.create(o, "D", investigation_id=inv.id)
    art = await rsvc.create(o, "A")
    await dsvc.add_article(o, dossier.id, art.id)   # art.dossier_id set, NOT investigation_id
    # contributor reads it through the dossier's investigation
    assert (await rsvc.get_viewable(c, art.id)).id == art.id
    with pytest.raises(Exception):  # noqa: B017
        await rsvc.get_viewable(x, art.id)


@pytest.mark.asyncio
async def test_direct_grant_composes_without_membership(services):
    inv, o, _c, _v, x, _o2 = await _inv_with_members(services)
    rsvc, isvc, perms = (
        services["report_svc"], services["investigation_svc"], services["perm_svc"])
    art = await rsvc.create(o, "A")
    await isvc.add_story(o, inv.id, art.id)
    # X is NOT a member — but a direct editor grant lets X edit (override composes)
    await perms.grant_access(art.id, x, "editor")
    await rsvc.save_document(x, art.id, {"version": 2, "tiptap": {}}, None)


# ── DOSSIER inheritance ──
@pytest.mark.asyncio
async def test_dossier_inheritance_matrix(services):
    inv, o, c, v, x, o2 = await _inv_with_members(services)
    rsvc, dsvc = services["report_svc"], services["dossier_svc"]
    drepo = services["dossier_repo"]
    dossier = await dsvc.create(o, "D", investigation_id=inv.id)
    art = await rsvc.create(c, "A")
    # viewer reads, cannot add an article
    assert (await dsvc.get(v, dossier.id)).id == dossier.id
    with pytest.raises(PermissionDenied):
        await dsvc.add_article(v, dossier.id, art.id)
    # contributor can add an article (edit)
    await dsvc.add_article(c, dossier.id, art.id)
    # outsider cannot even read
    with pytest.raises(PermissionDenied):
        await dsvc.get(x, dossier.id)
    # a second investigation owner deletes the dossier via inheritance
    await dsvc.delete(o2, dossier.id, content="orphan")
    assert await drepo.get_by_id(dossier.id) is None


# ── VIZ inheritance ──
@pytest.mark.asyncio
async def test_viz_inheritance_matrix(services):
    inv, o, c, v, x, o2 = await _inv_with_members(services)
    vsvc = services["visualization_svc"]
    vrepo = services["visualization_repo"]
    viz = await vsvc.create(o, "Chart", "chart_snapshot", {}, investigation_id=inv.id)
    # viewer reads
    assert (await vsvc.get(v, viz.id)).id == viz.id
    # contributor edits (rename)
    await vsvc.update(c, viz.id, "renamed")
    # viewer cannot edit
    with pytest.raises(PermissionDenied):
        await vsvc.update(v, viz.id, "nope")
    # outsider cannot read
    with pytest.raises(PermissionDenied):
        await vsvc.get(x, viz.id)
    # a second investigation owner deletes it via inheritance
    await vsvc.delete(o2, viz.id)
    assert await vrepo.get_by_id(viz.id) is None
