"""InMemoryInvestigationRepository behaviour (M1)."""
from __future__ import annotations

import pytest

from src.domain.investigation import Investigation, InvestigationMember
from src.infra.memory.mem_investigation_repo import InMemoryInvestigationRepository


def _repo():
    return InMemoryInvestigationRepository()


@pytest.mark.asyncio
async def test_create_assigns_id_and_timestamps():
    repo = _repo()
    inv = await repo.create(Investigation(name="X", description="d", created_by="u1"))
    assert inv.id and inv.created_at and inv.updated_at
    got = await repo.get_by_id(inv.id)
    assert got is not None and got.name == "X" and got.created_by == "u1"


@pytest.mark.asyncio
async def test_update_meta():
    repo = _repo()
    inv = await repo.create(Investigation(name="X", created_by="u1"))
    inv.name = "Y"
    inv.description = "z"
    upd = await repo.update(inv)
    assert upd.name == "Y" and upd.description == "z"
    assert (await repo.get_by_id(inv.id)).name == "Y"


@pytest.mark.asyncio
async def test_members_upsert_list_count_remove():
    repo = _repo()
    inv = await repo.create(Investigation(name="X", created_by="u1"))
    await repo.upsert_member(InvestigationMember(inv.id, "u1", is_owner=True))
    await repo.upsert_member(InvestigationMember(inv.id, "u2", can_write_stories=True))
    assert await repo.count_owners(inv.id) == 1
    assert len(await repo.list_members(inv.id)) == 2
    m2 = await repo.get_member(inv.id, "u2")
    assert m2 is not None and m2.can_write_stories and not m2.is_owner
    # upsert updates in place
    await repo.upsert_member(InvestigationMember(inv.id, "u2", is_owner=True))
    assert await repo.count_owners(inv.id) == 2
    await repo.remove_member(inv.id, "u2")
    assert await repo.get_member(inv.id, "u2") is None
    assert await repo.count_owners(inv.id) == 1


@pytest.mark.asyncio
async def test_list_for_user_and_delete():
    repo = _repo()
    inv = await repo.create(Investigation(name="A", created_by="u1"))
    await repo.upsert_member(InvestigationMember(inv.id, "u1", is_owner=True))
    assert len(await repo.list_for_user("u1")) == 1
    assert await repo.list_for_user("nobody") == []
    await repo.delete(inv.id)
    assert await repo.get_by_id(inv.id) is None
    assert await repo.list_for_user("u1") == []
