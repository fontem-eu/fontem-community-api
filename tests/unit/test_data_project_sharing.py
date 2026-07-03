"""Data Studio sharing — the permission dance (privilege escalation is the
biggest security risk here, so this suite is deliberately exhaustive).

Mirrors the visualization sharing model: a project attached to an investigation
lets members inherit access by role (viewer→read, contributor→edit, owner→own),
and per-user grants (viewer/commenter/editor/owner) add on top. SHARE + attach
need ownership or an admin seat.

Users: o = project creator + investigation owner; c = contributor member;
v = viewer member; x = outsider; o2 = a second investigation owner who did NOT
create the project (so its access is pure inheritance).
"""
from __future__ import annotations

import pytest

from src.services.exceptions import InvalidInput, NotFound, PermissionDenied
from tests.conftest import _stable_uuid, seed_user


async def _users(services, *names):
    for n in names:
        await seed_user(services["user_repo"], n)
    return [_stable_uuid(n) for n in names]


async def _project(services, owner):
    """A bare owner-private project (no investigation)."""
    return await services["data_project_svc"].create_project(owner, "P")


async def _shared_project(services):
    """A project attached to an investigation with o(owner)/c(contributor)/
    v(viewer)/o2(owner) members and x an outsider."""
    o, c, v, x, o2 = await _users(services, "o", "c", "v", "x", "o2")
    dsvc, isvc = services["data_project_svc"], services["investigation_svc"]
    inv = await isvc.create(o, "Inv")
    await isvc.set_member(o, inv.id, c, role="contributor")
    await isvc.set_member(o, inv.id, v, role="viewer")
    await isvc.set_member(o, inv.id, o2, role="owner")
    proj = await dsvc.create_project(o, "P", investigation_id=inv.id)
    return dsvc, isvc, inv, proj, o, c, v, x, o2


# ── ownership baseline ──────────────────────────────────────────
@pytest.mark.asyncio
async def test_owner_can_crud_stranger_denied(services):
    o, x = await _users(services, "o", "x")
    dsvc = services["data_project_svc"]
    p = await _project(services, o)
    # owner: full access
    assert (await dsvc.get_project(o, p.id)).id == p.id
    # a stranger with no investigation link + no grant: denied on read/edit/delete
    with pytest.raises(PermissionDenied):
        await dsvc.get_project(x, p.id)
    with pytest.raises(PermissionDenied):
        await dsvc.rename_project(x, p.id, "hijacked")
    with pytest.raises(PermissionDenied):
        await dsvc.delete_project(x, p.id)


@pytest.mark.asyncio
async def test_list_mine_is_owner_scoped(services):
    o, x = await _users(services, "o", "x")
    dsvc = services["data_project_svc"]
    p = await _project(services, o)
    assert [pr.id for pr in await dsvc.list_projects(o)] == [p.id]
    assert await dsvc.list_projects(x) == []  # x owns nothing


@pytest.mark.asyncio
async def test_unknown_project_is_404(services):
    (o,) = await _users(services, "o")
    with pytest.raises(NotFound):
        await services["data_project_svc"].get_project(
            o, "00000000-0000-0000-0000-000000000000")


# ── investigation inheritance matrix ────────────────────────────
@pytest.mark.asyncio
async def test_inheritance_matrix_read_edit_delete(services):
    dsvc, _isvc, _inv, proj, _o, c, v, x, o2 = await _shared_project(services)
    # viewer reads, cannot edit
    assert (await dsvc.get_project(v, proj.id)).id == proj.id
    with pytest.raises(PermissionDenied):
        await dsvc.rename_project(v, proj.id, "no")
    # contributor edits, cannot delete
    await dsvc.rename_project(c, proj.id, "by-contributor")
    with pytest.raises(PermissionDenied):
        await dsvc.delete_project(c, proj.id)
    # outsider: nothing
    with pytest.raises(PermissionDenied):
        await dsvc.get_project(x, proj.id)
    # a second investigation owner (never touched the project) can delete via
    # pure inheritance — proves role, not just creator identity, is honored.
    await dsvc.delete_project(o2, proj.id)


@pytest.mark.asyncio
async def test_subresources_gated_by_project_edit(services):
    """Queries + plots are sub-resources: viewer read-only, contributor writes."""
    dsvc, _isvc, _inv, proj, o, c, v, _x, _o2 = await _shared_project(services)
    q = await dsvc.add_query(o, proj.id, "q", "cypher", "MATCH (n) RETURN n")
    pl = await dsvc.add_plot(o, proj.id, "pl", {"chart": "bar_h"})
    # viewer cannot mutate queries or plots
    for call in (
        dsvc.add_query(v, proj.id, "x", "cypher", "..."),
        dsvc.update_query(v, proj.id, q.id, "x", None, None),
        dsvc.delete_query(v, proj.id, q.id),
        dsvc.duplicate_query(v, proj.id, q.id),
        dsvc.add_plot(v, proj.id, "x", {}),
        dsvc.update_plot(v, proj.id, pl.id, "x", None),
        dsvc.delete_plot(v, proj.id, pl.id),
    ):
        with pytest.raises(PermissionDenied):
            await call
    # contributor can
    await dsvc.update_query(c, proj.id, q.id, "renamed", None, None)
    await dsvc.update_plot(c, proj.id, pl.id, "renamed", None)


# ── attach / detach ─────────────────────────────────────────────
@pytest.mark.asyncio
async def test_attach_detach_and_list_for_investigation(services):
    o, x = await _users(services, "o", "x")
    dsvc, isvc = services["data_project_svc"], services["investigation_svc"]
    inv = await isvc.create(o, "Inv")
    p = await _project(services, o)
    await dsvc.attach(o, p.id, inv.id)
    assert [pr.id for pr in await dsvc.list_for_investigation(o, inv.id)] == [p.id]
    # outsider cannot list the investigation's projects (needs READ membership)
    with pytest.raises(PermissionDenied):
        await dsvc.list_for_investigation(x, inv.id)
    await dsvc.detach(o, p.id)
    assert await dsvc.list_for_investigation(o, inv.id) == []


@pytest.mark.asyncio
async def test_attach_requires_add_on_target_investigation(services):
    o, x = await _users(services, "o", "x")
    dsvc, isvc = services["data_project_svc"], services["investigation_svc"]
    inv_other = await isvc.create(x, "Theirs")  # o is not a member
    p = await _project(services, o)
    # o owns the project but has no seat on x's investigation → cannot attach
    with pytest.raises(PermissionDenied):
        await dsvc.attach(o, p.id, inv_other.id)


@pytest.mark.asyncio
async def test_create_on_investigation_requires_contributor(services):
    o, c, viewer = await _users(services, "o", "c", "viewer")
    dsvc, isvc = services["data_project_svc"], services["investigation_svc"]
    inv = await isvc.create(o, "Inv")
    await isvc.set_member(o, inv.id, c, role="contributor")
    await isvc.set_member(o, inv.id, viewer, role="viewer")
    await dsvc.create_project(o, "by owner", investigation_id=inv.id)
    await dsvc.create_project(c, "by contributor", investigation_id=inv.id)
    with pytest.raises(PermissionDenied):
        await dsvc.create_project(viewer, "by viewer", investigation_id=inv.id)
    assert len(await dsvc.list_for_investigation(o, inv.id)) == 2


# ── direct grants + privilege escalation ────────────────────────
@pytest.mark.asyncio
async def test_direct_grant_read_then_edit_then_revoke(services):
    o, x = await _users(services, "o", "x")
    dsvc = services["data_project_svc"]
    p = await _project(services, o)
    # no grant: denied
    with pytest.raises(PermissionDenied):
        await dsvc.get_project(x, p.id)
    # viewer grant: read yes, edit no
    await dsvc.share(o, p.id, x, level="viewer")
    assert (await dsvc.get_project(x, p.id)).id == p.id
    with pytest.raises(PermissionDenied):
        await dsvc.rename_project(x, p.id, "no")
    # editor grant: edit yes
    await dsvc.share(o, p.id, x, level="editor")
    await dsvc.rename_project(x, p.id, "yes")
    # revoke: back to denied
    await dsvc.revoke(o, p.id, x)
    with pytest.raises(PermissionDenied):
        await dsvc.get_project(x, p.id)


@pytest.mark.asyncio
async def test_only_owner_or_admin_can_share(services):
    dsvc, _isvc, _inv, proj, _o, c, v, x, o2 = await _shared_project(services)
    # a contributor cannot manage grants
    with pytest.raises(PermissionDenied):
        await dsvc.share(c, proj.id, x, level="viewer")
    # a viewer certainly cannot (privilege escalation attempt)
    with pytest.raises(PermissionDenied):
        await dsvc.share(v, proj.id, v, level="editor")
    # an admin/owner investigation member can
    await dsvc.share(o2, proj.id, x, level="viewer")


@pytest.mark.asyncio
async def test_granted_editor_cannot_reshare(services):
    """A direct editor grant confers edit, NOT the ability to re-share — the
    SHARE gate has no grant path, closing a classic escalation."""
    o, x, y = await _users(services, "o", "x", "y")
    dsvc = services["data_project_svc"]
    p = await _project(services, o)
    await dsvc.share(o, p.id, x, level="editor")
    await dsvc.rename_project(x, p.id, "x can edit")  # sanity: grant works
    with pytest.raises(PermissionDenied):
        await dsvc.share(x, p.id, y, level="editor")  # but cannot re-share
    with pytest.raises(PermissionDenied):
        await dsvc.revoke(x, p.id, x)  # nor revoke


@pytest.mark.asyncio
async def test_viewer_cannot_detach_to_escape_sharing(services):
    """Detaching drops the project back to owner-private; a viewer must not be
    able to do it (it needs EDIT)."""
    dsvc, _isvc, _inv, proj, _o, _c, v, _x, _o2 = await _shared_project(services)
    with pytest.raises(PermissionDenied):
        await dsvc.detach(v, proj.id)


@pytest.mark.asyncio
async def test_invalid_share_level_rejected(services):
    o, x = await _users(services, "o", "x")
    dsvc = services["data_project_svc"]
    p = await _project(services, o)
    with pytest.raises(InvalidInput):
        await dsvc.share(o, p.id, x, level="superuser")


@pytest.mark.asyncio
async def test_share_by_email(services):
    o, x = await _users(services, "o", "x")
    dsvc = services["data_project_svc"]
    p = await _project(services, o)
    await dsvc.share(o, p.id, target_email="x@test.com", level="editor")
    await dsvc.rename_project(x, p.id, "granted by email")


# ── effective access ────────────────────────────────────────────
@pytest.mark.asyncio
async def test_effective_access_owner_inherited_direct(services):
    dsvc, _isvc, _inv, proj, o, c, v, x, _o2 = await _shared_project(services)
    await dsvc.share(o, proj.id, x, level="editor")
    rows = await dsvc.effective_access(o, proj.id)
    by_user = {r["user_id"]: r for r in rows}
    assert by_user[o]["source"] == "owner"
    assert by_user[c]["level"] == "editor" and by_user[c]["source"].startswith("inherited")
    assert by_user[v]["level"] == "viewer" and by_user[v]["source"].startswith("inherited")
    assert by_user[x]["level"] == "editor" and by_user[x]["source"] == "direct"
    # a viewer can read the effective-access list but a genuine outsider cannot
    # (x now holds a direct grant, so use a fresh unrelated user here)
    (stranger,) = await _users(services, "stranger")
    assert await dsvc.effective_access(v, proj.id)
    with pytest.raises(PermissionDenied):
        await dsvc.effective_access(stranger, proj.id)


# ── access_flags (drives the client's read-only mode) ───────────
@pytest.mark.asyncio
async def test_access_flags_reflect_role(services):
    dsvc, _isvc, _inv, proj, o, c, v, x, _o2 = await _shared_project(services)
    await dsvc.share(o, proj.id, x, level="editor")

    async def flags(uid):
        return await dsvc.access_flags(uid, await dsvc._load(proj.id))  # noqa: SLF001

    owner = await flags(o)
    assert owner == {"level": "owner", "can_edit": True, "can_delete": True, "can_share": True}
    contributor = await flags(c)
    assert contributor["can_edit"] and not contributor["can_delete"] and not contributor["can_share"]
    assert contributor["level"] == "editor"
    viewer = await flags(v)
    assert viewer["level"] == "viewer" and not viewer["can_edit"]
    granted = await flags(x)  # editor grant
    assert granted["can_edit"] and not granted["can_share"]
