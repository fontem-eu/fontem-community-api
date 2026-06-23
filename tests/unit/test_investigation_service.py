"""InvestigationService — CRUD, membership, and the owner invariants (M2)."""
from __future__ import annotations

import pytest

from src.services.exceptions import Conflict, NotFound, PermissionDenied
from tests.conftest import _stable_uuid, seed_user


async def _users(services, *names):
    for n in names:
        await seed_user(services["user_repo"], n)
    return [_stable_uuid(n) for n in names]


@pytest.mark.asyncio
async def test_create_makes_creator_owner(services):
    (u1,) = await _users(services, "u1")
    svc = services["investigation_svc"]
    inv = await svc.create(u1, "My Inv", "desc")
    assert inv.id and inv.created_by == u1
    m = await svc.my_membership(u1, inv.id)
    assert m is not None
    assert m.is_owner and m.can_administer and m.can_write_stories and m.can_add_viz


@pytest.mark.asyncio
async def test_nonmember_cannot_read(services):
    u1, u2 = await _users(services, "u1", "u2")
    svc = services["investigation_svc"]
    inv = await svc.create(u1, "I")
    with pytest.raises(PermissionDenied):
        await svc.get(u2, inv.id)


@pytest.mark.asyncio
async def test_member_can_read_after_added(services):
    u1, u2 = await _users(services, "u1", "u2")
    svc = services["investigation_svc"]
    inv = await svc.create(u1, "I")
    await svc.set_member(u1, inv.id, u2, can_write_stories=True)
    got = await svc.get(u2, inv.id)
    assert got.id == inv.id


@pytest.mark.asyncio
async def test_get_unknown_is_404(services):
    (u1,) = await _users(services, "u1")
    svc = services["investigation_svc"]
    with pytest.raises(NotFound):
        await svc.get(u1, "00000000-0000-0000-0000-000000000000")


@pytest.mark.asyncio
async def test_administer_can_edit_meta_noncap_cannot(services):
    u1, u2, u3 = await _users(services, "u1", "u2", "u3")
    svc = services["investigation_svc"]
    inv = await svc.create(u1, "I")
    await svc.set_member(u1, inv.id, u2, can_administer=True)
    await svc.set_member(u1, inv.id, u3, can_write_stories=True)  # no administer
    upd = await svc.update_meta(u2, inv.id, name="Renamed")
    assert upd.name == "Renamed"
    with pytest.raises(PermissionDenied):
        await svc.update_meta(u3, inv.id, name="Nope")


@pytest.mark.asyncio
async def test_only_owner_can_grant_owner(services):
    u1, u2, u3 = await _users(services, "u1", "u2", "u3")
    svc = services["investigation_svc"]
    inv = await svc.create(u1, "I")
    await svc.set_member(u1, inv.id, u2, can_administer=True)  # admin, not owner
    with pytest.raises(PermissionDenied):
        await svc.set_member(u2, inv.id, u3, is_owner=True)


@pytest.mark.asyncio
async def test_owner_can_promote_to_owner(services):
    u1, u2 = await _users(services, "u1", "u2")
    svc = services["investigation_svc"]
    inv = await svc.create(u1, "I")
    await svc.set_member(u1, inv.id, u2, can_administer=True, is_owner=True)
    m2 = await svc.my_membership(u2, inv.id)
    assert m2.is_owner


@pytest.mark.asyncio
async def test_cannot_change_another_owner(services):
    u1, u2 = await _users(services, "u1", "u2")
    svc = services["investigation_svc"]
    inv = await svc.create(u1, "I")
    await svc.set_member(u1, inv.id, u2, is_owner=True, can_administer=True)
    with pytest.raises(Conflict):
        await svc.set_member(u1, inv.id, u2, can_write_stories=True)  # u2 is another owner


@pytest.mark.asyncio
async def test_cannot_remove_another_owner(services):
    u1, u2 = await _users(services, "u1", "u2")
    svc = services["investigation_svc"]
    inv = await svc.create(u1, "I")
    await svc.set_member(u1, inv.id, u2, is_owner=True)
    with pytest.raises(Conflict):
        await svc.remove_member(u1, inv.id, u2)


@pytest.mark.asyncio
async def test_cannot_demote_last_owner(services):
    (u1,) = await _users(services, "u1")
    svc = services["investigation_svc"]
    inv = await svc.create(u1, "I")
    with pytest.raises(Conflict):
        await svc.set_member(u1, inv.id, u1, is_owner=False, can_administer=True)


@pytest.mark.asyncio
async def test_cannot_remove_last_owner(services):
    (u1,) = await _users(services, "u1")
    svc = services["investigation_svc"]
    inv = await svc.create(u1, "I")
    with pytest.raises(Conflict):
        await svc.remove_member(u1, inv.id, u1)


@pytest.mark.asyncio
async def test_delete_requires_owner(services):
    u1, u2 = await _users(services, "u1", "u2")
    svc = services["investigation_svc"]
    inv = await svc.create(u1, "I")
    await svc.set_member(u1, inv.id, u2, can_administer=True)  # admin, not owner
    with pytest.raises(PermissionDenied):
        await svc.delete(u2, inv.id)
    await svc.delete(u1, inv.id)  # owner can
    with pytest.raises(NotFound):
        await svc.get(u1, inv.id)


@pytest.mark.asyncio
async def test_invite_member_by_email(services):
    await seed_user(services["user_repo"], "u1")
    await seed_user(services["user_repo"], "u2")  # email seeded as u2@test.com
    svc = services["investigation_svc"]
    u1 = _stable_uuid("u1")
    inv = await svc.create(u1, "I")
    await svc.set_member(u1, inv.id, target_email="u2@test.com", can_write_stories=True)
    member = await svc.my_membership(_stable_uuid("u2"), inv.id)
    assert member is not None and member.can_write_stories


@pytest.mark.asyncio
async def test_invite_unknown_email_404(services):
    await seed_user(services["user_repo"], "u1")
    svc = services["investigation_svc"]
    u1 = _stable_uuid("u1")
    inv = await svc.create(u1, "I")
    with pytest.raises(NotFound):
        await svc.set_member(u1, inv.id, target_email="nobody@x.io")
