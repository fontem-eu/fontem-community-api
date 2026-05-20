"""Tag-related tests: slug normalisation, story-side limits,
follow/unfollow caps, and the API endpoints."""
from __future__ import annotations

import pytest

from src.services.exceptions import InvalidInput, NotFound, PermissionDenied
from src.services.tag_service import (
    MAX_FOLLOWED_TAGS_PER_USER,
    MAX_TAGS_PER_STORY,
    normalise_tag,
    normalise_tags,
)
from tests.conftest import seed_user, make_headers


# ── normalise_tag — pure function ─────────────────────────────


@pytest.mark.parametrize("raw,expected", [
    ("Public Expenditure", "public-expenditure"),
    ("  procurement!  ", "procurement"),
    ("MIGRATION ___ flows", "migration-flows"),
    ("Already-OK", "already-ok"),
    ("emoji 😀 stripped", "emoji-stripped"),
    ("number-1-then-text", "number-1-then-text"),
])
def test_normalise_tag_canonical_forms(raw, expected):
    assert normalise_tag(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "!!!", "😀😀😀"])
def test_normalise_tag_rejects_empty_results(raw):
    with pytest.raises(InvalidInput):
        normalise_tag(raw)


def test_normalise_tag_caps_length_at_40():
    long = "a" * 60
    assert len(normalise_tag(long)) == 40


def test_normalise_tags_dedupes_and_preserves_order():
    out = normalise_tags(["procurement", "Procurement", "lobbying"])
    # "procurement" and "Procurement" both slug to "procurement" — the
    # first wins, second is dropped, "lobbying" survives.
    assert out == ["procurement", "lobbying"]


# ── Story tags (service layer) ────────────────────────────────


@pytest.mark.asyncio
async def test_set_story_tags_normalises_and_persists(services):
    user = await seed_user(services["user_repo"], "owner")
    report = await services["report_svc"].create(user.id, "title", None)
    saved = await services["tag_svc"].set_story_tags(
        user.id, report.id, ["Public Expenditure", "  procurement  "],
    )
    assert saved == ["public-expenditure", "procurement"]
    fetched = await services["tag_svc"].get_story_tags(report.id)
    assert fetched == ["procurement", "public-expenditure"]  # repo sorts


@pytest.mark.asyncio
async def test_set_story_tags_rejects_more_than_three(services):
    user = await seed_user(services["user_repo"], "owner")
    report = await services["report_svc"].create(user.id, "t", None)
    with pytest.raises(InvalidInput, match=str(MAX_TAGS_PER_STORY)):
        await services["tag_svc"].set_story_tags(
            user.id, report.id,
            ["a", "b", "c", "d"],  # four → reject
        )


@pytest.mark.asyncio
async def test_set_story_tags_rejects_non_owner(services):
    owner = await seed_user(services["user_repo"], "owner")
    other = await seed_user(services["user_repo"], "other")
    report = await services["report_svc"].create(owner.id, "t", None)
    with pytest.raises(PermissionDenied):
        await services["tag_svc"].set_story_tags(other.id, report.id, ["x"])


@pytest.mark.asyncio
async def test_set_story_tags_404s_on_missing_story(services):
    user = await seed_user(services["user_repo"], "owner")
    with pytest.raises(NotFound):
        await services["tag_svc"].set_story_tags(
            user.id, "00000000-0000-4000-8000-000000000000", ["x"],
        )


# ── Followed tags (service layer) ─────────────────────────────


@pytest.mark.asyncio
async def test_follow_normalises_and_dedupes(services):
    user = await seed_user(services["user_repo"], "u")
    await services["tag_svc"].follow(user.id, "Public Expenditure")
    await services["tag_svc"].follow(user.id, "public-expenditure")  # same
    assert await services["tag_svc"].list_followed(user.id) == ["public-expenditure"]


@pytest.mark.asyncio
async def test_follow_rejects_after_50(services):
    user = await seed_user(services["user_repo"], "u")
    for i in range(MAX_FOLLOWED_TAGS_PER_USER):
        await services["tag_svc"].follow(user.id, f"tag-{i}")
    with pytest.raises(InvalidInput, match=str(MAX_FOLLOWED_TAGS_PER_USER)):
        await services["tag_svc"].follow(user.id, "tag-overflow")


@pytest.mark.asyncio
async def test_unfollow_is_idempotent(services):
    user = await seed_user(services["user_repo"], "u")
    await services["tag_svc"].follow(user.id, "public-expenditure")
    await services["tag_svc"].unfollow(user.id, "public-expenditure")
    await services["tag_svc"].unfollow(user.id, "public-expenditure")  # no-op
    assert await services["tag_svc"].list_followed(user.id) == []


# ── Tag-filtered list_public ──────────────────────────────────


@pytest.mark.asyncio
async def test_list_public_filters_by_tag(services):
    user = await seed_user(services["user_repo"], "owner")
    a = await services["report_svc"].create(user.id, "A", None)
    b = await services["report_svc"].create(user.id, "B", None)
    # Make both public_open
    a.visibility = "public_open"
    await services["report_repo"].update(a)
    b.visibility = "public_open"
    await services["report_repo"].update(b)
    await services["tag_svc"].set_story_tags(user.id, a.id, ["procurement"])
    await services["tag_svc"].set_story_tags(user.id, b.id, ["lobbying"])

    procurement = await services["report_svc"].list_public(
        limit=10, offset=0, tag="procurement",
    )
    assert [r.id for r in procurement] == [a.id]


# ── list_distinct_tags ────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_distinct_tags_excludes_private(services):
    user = await seed_user(services["user_repo"], "owner")
    pub = await services["report_svc"].create(user.id, "P", None)
    priv = await services["report_svc"].create(user.id, "Q", None)
    pub.visibility = "public_open"
    await services["report_repo"].update(pub)
    # priv stays private
    await services["tag_svc"].set_story_tags(user.id, pub.id, ["public-expenditure"])
    await services["tag_svc"].set_story_tags(user.id, priv.id, ["leak-tag"])

    tags = await services["tag_svc"].list_distinct_tags()
    tag_names = [t for t, _ in tags]
    assert "public-expenditure" in tag_names
    assert "leak-tag" not in tag_names  # private story's tag stays hidden


# ── API endpoints ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_put_story_tags_owner_only(client, services):
    owner = await seed_user(services["user_repo"], "owner")
    await seed_user(services["user_repo"], "other")
    report = await services["report_svc"].create(owner.id, "t", None)

    r = client.put(
        f"/data-stories/{report.id}/tags",
        json={"tags": ["public-expenditure"]},
        headers=make_headers("owner"),
    )
    assert r.status_code == 200
    assert r.json()["tags"] == ["public-expenditure"]

    r = client.put(
        f"/data-stories/{report.id}/tags",
        json={"tags": ["other-tag"]},
        headers=make_headers("other"),
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_get_tags_endpoint_returns_distinct(client, services):
    user = await seed_user(services["user_repo"], "u")
    a = await services["report_svc"].create(user.id, "A", None)
    a.visibility = "public_open"
    await services["report_repo"].update(a)
    await services["tag_svc"].set_story_tags(user.id, a.id, ["public-expenditure"])

    r = client.get("/tags")
    assert r.status_code == 200
    body = r.json()
    assert body["tags"][0]["tag"] == "public-expenditure"
    assert body["tags"][0]["story_count"] == 1
    assert body["limits"]["max_per_story"] == MAX_TAGS_PER_STORY


@pytest.mark.asyncio
async def test_followed_tags_round_trip(client, services):
    await seed_user(services["user_repo"], "u")
    headers = make_headers("u")

    r = client.post("/me/followed-tags", json={"tag": "Public Expenditure"}, headers=headers)
    assert r.status_code == 201
    assert r.json()["tag"] == "public-expenditure"

    r = client.get("/me/followed-tags", headers=headers)
    assert r.json()["tags"] == ["public-expenditure"]

    r = client.delete("/me/followed-tags/public-expenditure", headers=headers)
    assert r.status_code == 204

    r = client.get("/me/followed-tags", headers=headers)
    assert r.json()["tags"] == []


@pytest.mark.asyncio
async def test_list_reports_embeds_tags_and_filters(client, services):
    user = await seed_user(services["user_repo"], "u")
    a = await services["report_svc"].create(user.id, "A", None)
    b = await services["report_svc"].create(user.id, "B", None)
    a.visibility = "public_open"
    await services["report_repo"].update(a)
    b.visibility = "public_open"
    await services["report_repo"].update(b)
    await services["tag_svc"].set_story_tags(user.id, a.id, ["procurement"])
    await services["tag_svc"].set_story_tags(user.id, b.id, ["lobbying"])

    r = client.get("/data-stories?scope=public")
    assert r.status_code == 200
    rows = r.json()
    by_id = {row["id"]: row for row in rows}
    assert by_id[a.id]["tags"] == ["procurement"]
    assert by_id[b.id]["tags"] == ["lobbying"]

    r = client.get("/data-stories?scope=public&tag=procurement")
    rows = r.json()
    assert [row["id"] for row in rows] == [a.id]
