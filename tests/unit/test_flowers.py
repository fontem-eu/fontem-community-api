"""Flower (Medium-style clap) tests.

Covers the service-layer policy (50-cap, visibility check), the
in-memory repo, and the HTTP round-trip through both /data-stories
and /reports prefixes.
"""
from __future__ import annotations

import asyncio

import pytest

from src.services.exceptions import InvalidInput, NotFound
from src.services.flower_service import MAX_FLOWERS_PER_USER
from tests.conftest import make_headers, seed_user


# ── Service-layer policy ──────────────────────────────────────


async def _make_public_report(services, user, title: str = "t") -> str:
    """Create a story owned by ``user`` and flip it to public_open so
    flowers can be given to it.

    The flower service refuses to operate on stories that aren't
    publicly visible (private stories shouldn't be probable via
    /flowers), so every flower test needs one of these.
    """
    report = await services["report_svc"].create(user.id, title, None)
    report.visibility = "public_open"
    await services["report_repo"].update(report)
    return report.id


@pytest.mark.asyncio
async def test_get_state_empty_when_no_flowers_given(services):
    owner = await seed_user(services["user_repo"], "owner")
    report_id = await _make_public_report(services, owner)
    state = await services["flower_svc"].get_state(owner.id, report_id)
    assert state == {"total": 0, "mine": 0}


@pytest.mark.asyncio
async def test_get_state_anonymous_returns_zero_mine(services):
    owner = await seed_user(services["user_repo"], "owner")
    report_id = await _make_public_report(services, owner)
    # Owner gives one flower, then an anonymous read should see the
    # total but not anyone's per-user count.
    await services["flower_svc"].give(owner.id, report_id)
    state = await services["flower_svc"].get_state(None, report_id)
    assert state == {"total": 1, "mine": 0}


@pytest.mark.asyncio
async def test_give_increments_both_total_and_mine(services):
    owner = await seed_user(services["user_repo"], "owner")
    report_id = await _make_public_report(services, owner)
    s1 = await services["flower_svc"].give(owner.id, report_id)
    assert s1 == {"total": 1, "mine": 1}
    s2 = await services["flower_svc"].give(owner.id, report_id)
    assert s2 == {"total": 2, "mine": 2}


@pytest.mark.asyncio
async def test_two_users_each_get_independent_mine_but_shared_total(services):
    owner = await seed_user(services["user_repo"], "owner")
    other = await seed_user(services["user_repo"], "other")
    report_id = await _make_public_report(services, owner)
    await services["flower_svc"].give(owner.id, report_id)
    await services["flower_svc"].give(owner.id, report_id)
    await services["flower_svc"].give(other.id, report_id)
    owner_state = await services["flower_svc"].get_state(owner.id, report_id)
    other_state = await services["flower_svc"].get_state(other.id, report_id)
    assert owner_state == {"total": 3, "mine": 2}
    assert other_state == {"total": 3, "mine": 1}


@pytest.mark.asyncio
async def test_give_rejects_after_cap(services):
    owner = await seed_user(services["user_repo"], "owner")
    report_id = await _make_public_report(services, owner)
    for _ in range(MAX_FLOWERS_PER_USER):
        await services["flower_svc"].give(owner.id, report_id)
    with pytest.raises(InvalidInput, match=str(MAX_FLOWERS_PER_USER)):
        await services["flower_svc"].give(owner.id, report_id)


@pytest.mark.asyncio
async def test_get_state_404s_on_missing_story(services):
    owner = await seed_user(services["user_repo"], "owner")
    with pytest.raises(NotFound):
        await services["flower_svc"].get_state(
            owner.id, "00000000-0000-4000-8000-000000000000",
        )


@pytest.mark.asyncio
async def test_get_state_404s_on_private_story(services):
    owner = await seed_user(services["user_repo"], "owner")
    # Private by default (no visibility flip) — should be invisible to
    # the flower endpoint even for the owner.
    report = await services["report_svc"].create(owner.id, "secret", None)
    with pytest.raises(NotFound):
        await services["flower_svc"].get_state(owner.id, report.id)


@pytest.mark.asyncio
async def test_give_404s_on_private_story(services):
    owner = await seed_user(services["user_repo"], "owner")
    report = await services["report_svc"].create(owner.id, "secret", None)
    with pytest.raises(NotFound):
        await services["flower_svc"].give(owner.id, report.id)


# ── HTTP round-trip ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_post_flowers_round_trip(client, services):
    owner = await seed_user(services["user_repo"], "owner")
    report_id = await _make_public_report(services, owner)

    r = client.post(
        f"/data-stories/{report_id}/flowers",
        headers=make_headers("owner"),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["mine"] == 1
    assert body["max_per_user"] == MAX_FLOWERS_PER_USER

    # Second click bumps both numbers.
    r = client.post(
        f"/data-stories/{report_id}/flowers",
        headers=make_headers("owner"),
    )
    assert r.status_code == 200
    assert r.json()["mine"] == 2


@pytest.mark.asyncio
async def test_get_flowers_returns_state_for_anon(client, services):
    owner = await seed_user(services["user_repo"], "owner")
    report_id = await _make_public_report(services, owner)
    # Owner gives two flowers via the service so the GET sees a total.
    await services["flower_svc"].give(owner.id, report_id)
    await services["flower_svc"].give(owner.id, report_id)

    r = client.get(f"/data-stories/{report_id}/flowers")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert body["mine"] == 0  # anon


@pytest.mark.asyncio
async def test_get_flowers_returns_mine_for_authed(client, services):
    owner = await seed_user(services["user_repo"], "owner")
    other = await seed_user(services["user_repo"], "other")
    report_id = await _make_public_report(services, owner)
    await services["flower_svc"].give(owner.id, report_id)
    await services["flower_svc"].give(owner.id, report_id)
    await services["flower_svc"].give(other.id, report_id)

    r = client.get(
        f"/data-stories/{report_id}/flowers",
        headers=make_headers("owner"),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3
    assert body["mine"] == 2


@pytest.mark.asyncio
async def test_post_flowers_caps_at_max(client, services):
    owner = await seed_user(services["user_repo"], "owner")
    report_id = await _make_public_report(services, owner)
    for _ in range(MAX_FLOWERS_PER_USER):
        r = client.post(
            f"/data-stories/{report_id}/flowers",
            headers=make_headers("owner"),
        )
        assert r.status_code == 200
    # 51st click — 400 with the cap message.
    r = client.post(
        f"/data-stories/{report_id}/flowers",
        headers=make_headers("owner"),
    )
    assert r.status_code == 400
    assert str(MAX_FLOWERS_PER_USER) in r.json()["detail"]


@pytest.mark.asyncio
async def test_post_flowers_requires_auth(client, services):
    owner = await seed_user(services["user_repo"], "owner")
    report_id = await _make_public_report(services, owner)
    r = client.post(f"/data-stories/{report_id}/flowers")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_post_flowers_404s_on_missing_story(client, services):
    await seed_user(services["user_repo"], "owner")
    r = client.post(
        "/data-stories/00000000-0000-4000-8000-000000000000/flowers",
        headers=make_headers("owner"),
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_post_flowers_404s_on_private_story(client, services):
    owner = await seed_user(services["user_repo"], "owner")
    report = await services["report_svc"].create(owner.id, "secret", None)
    r = client.post(
        f"/data-stories/{report.id}/flowers",
        headers=make_headers("owner"),
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_flowers_router_dual_mount_under_reports_prefix(client, services):
    """The router is mounted at /data-stories AND /reports — the
    deprecated alias must keep working through the rename window."""
    owner = await seed_user(services["user_repo"], "owner")
    report_id = await _make_public_report(services, owner)
    r = client.post(
        f"/reports/{report_id}/flowers",
        headers=make_headers("owner"),
    )
    assert r.status_code == 200
    assert r.json()["mine"] == 1


# ── Visibility split: anon vs authed on public_auth ──────────


async def _make_report_with_visibility(services, user, vis: str) -> str:
    report = await services["report_svc"].create(user.id, "t", None)
    report.visibility = vis
    await services["report_repo"].update(report)
    return report.id


@pytest.mark.asyncio
async def test_get_state_authed_sees_public_auth_story(services):
    owner = await seed_user(services["user_repo"], "owner")
    report_id = await _make_report_with_visibility(services, owner, "public_auth")
    state = await services["flower_svc"].get_state(owner.id, report_id)
    assert state == {"total": 0, "mine": 0}


@pytest.mark.asyncio
async def test_get_state_anon_404s_on_public_auth_story(services):
    """public_auth is login-walled, so the flower total must not leak
    to an anonymous caller — mirror ReportService.get_viewable."""
    owner = await seed_user(services["user_repo"], "owner")
    report_id = await _make_report_with_visibility(services, owner, "public_auth")
    with pytest.raises(NotFound):
        await services["flower_svc"].get_state(None, report_id)


@pytest.mark.asyncio
async def test_get_flowers_http_anon_404s_on_public_auth(client, services):
    owner = await seed_user(services["user_repo"], "owner")
    report_id = await _make_report_with_visibility(services, owner, "public_auth")
    r = client.get(f"/data-stories/{report_id}/flowers")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_get_flowers_http_authed_200s_on_public_auth(client, services):
    owner = await seed_user(services["user_repo"], "owner")
    await seed_user(services["user_repo"], "viewer")
    report_id = await _make_report_with_visibility(services, owner, "public_auth")
    r = client.get(
        f"/data-stories/{report_id}/flowers",
        headers=make_headers("viewer"),
    )
    assert r.status_code == 200
    assert r.json()["total"] == 0


# ── Non-owner can clap another user's public story ────────────


@pytest.mark.asyncio
async def test_non_owner_can_give_flower_on_public_story(client, services):
    owner = await seed_user(services["user_repo"], "owner")
    await seed_user(services["user_repo"], "other")
    report_id = await _make_public_report(services, owner)

    # Owner gives one, other gives one — totals add up across users,
    # each user's `mine` is independent. Locks in the FromDishka
    # user-id routing through the auth dependency.
    r1 = client.post(
        f"/data-stories/{report_id}/flowers",
        headers=make_headers("owner"),
    )
    r2 = client.post(
        f"/data-stories/{report_id}/flowers",
        headers=make_headers("other"),
    )
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["mine"] == 1 and r2.json()["mine"] == 1
    assert r1.json()["total"] == 1 and r2.json()["total"] == 2

    g_owner = client.get(
        f"/data-stories/{report_id}/flowers",
        headers=make_headers("owner"),
    ).json()
    g_other = client.get(
        f"/data-stories/{report_id}/flowers",
        headers=make_headers("other"),
    ).json()
    assert g_owner == {"total": 2, "mine": 1, "max_per_user": MAX_FLOWERS_PER_USER}
    assert g_other == {"total": 2, "mine": 1, "max_per_user": MAX_FLOWERS_PER_USER}


# ── Concurrency: cap survives parallel POSTs ──────────────────


@pytest.mark.asyncio
async def test_concurrent_gives_never_exceed_cap(services):
    """Atomic upsert plus the cap-in-WHERE clause must hold even when
    many `give` calls race against each other for the same user.

    The in-memory repo serialises calls via the asyncio loop and the
    cap is enforced inside increment() itself, so this exercises the
    contract the Postgres impl mirrors with `ON CONFLICT DO UPDATE
    ... WHERE count < cap RETURNING count`.
    """
    owner = await seed_user(services["user_repo"], "owner")
    report_id = await _make_public_report(services, owner)
    # Fire 2× cap concurrent gives — every one beyond the cap must be
    # rejected with InvalidInput, none silently exceed the cap.
    n = MAX_FLOWERS_PER_USER * 2
    results = await asyncio.gather(
        *(services["flower_svc"].give(owner.id, report_id) for _ in range(n)),
        return_exceptions=True,
    )
    ok = [r for r in results if not isinstance(r, BaseException)]
    rejected = [r for r in results if isinstance(r, InvalidInput)]
    assert len(ok) == MAX_FLOWERS_PER_USER
    assert len(rejected) == MAX_FLOWERS_PER_USER
    final = await services["flower_svc"].get_state(owner.id, report_id)
    assert final == {"total": MAX_FLOWERS_PER_USER, "mine": MAX_FLOWERS_PER_USER}


# ── Repo-level cap signal ─────────────────────────────────────


@pytest.mark.asyncio
async def test_repo_increment_returns_none_at_cap(services):
    repo = services["flower_repo"]
    owner = await seed_user(services["user_repo"], "owner")
    report_id = await _make_public_report(services, owner)
    for _ in range(MAX_FLOWERS_PER_USER):
        result = await repo.increment(owner.id, report_id, cap=MAX_FLOWERS_PER_USER)
        assert result is not None
    # Next call is at the cap — the repo signals refusal via None,
    # not by raising; service layer translates that to InvalidInput.
    assert await repo.increment(owner.id, report_id, cap=MAX_FLOWERS_PER_USER) is None
