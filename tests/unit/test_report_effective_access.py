"""Parity — 'who has access & why' for articles (owner / inherited / direct)."""
from __future__ import annotations

import pytest

from tests.conftest import _stable_uuid, seed_user


async def _users(services, *names):
    for n in names:
        await seed_user(services["user_repo"], n)
    return [_stable_uuid(n) for n in names]


@pytest.mark.asyncio
async def test_article_effective_access_owner_inherited_direct(services):
    o, c, x = await _users(services, "o", "c", "x")
    isvc, rsvc, perms = (
        services["investigation_svc"], services["report_svc"], services["perm_svc"])
    inv = await isvc.create(o, "Inv")
    await isvc.set_member(o, inv.id, c, role="contributor")
    art = await rsvc.create(o, "A")
    await isvc.add_story(o, inv.id, art.id)
    await perms.grant_access(art.id, x, "viewer")  # a direct report_access grant

    rows = await rsvc.effective_access(o, art.id)
    by_user = {r.get("user_id"): r for r in rows if r.get("user_id")}
    assert by_user[o]["level"] == "owner" and by_user[o]["source"] == "owner"
    assert by_user[c]["level"] == "editor" and by_user[c]["source"] == "inherited:contributor"
    assert by_user[x]["level"] == "viewer" and by_user[x]["source"] == "direct"


@pytest.mark.asyncio
class TestReportEffectiveAccessAPI:
    async def _s(self, services, *names):
        for n in names:
            await seed_user(services["user_repo"], n)

    def test_endpoint_returns_owner(self, client, services):
        import asyncio
        from tests.conftest import make_headers
        asyncio.get_event_loop().run_until_complete(self._s(services, "user-1"))
        h = make_headers("user-1")
        sid = client.post("/data-stories", json={"title": "A", "abstract": ""}, headers=h).json()["id"]
        resp = client.get(f"/data-stories/{sid}/effective-access", headers=h)
        assert resp.status_code == 200
        assert any(r["source"] == "owner" for r in resp.json())
