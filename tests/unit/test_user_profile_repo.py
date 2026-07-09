"""InMemory user-profile repo: upsert/get roundtrip + replace semantics."""
from __future__ import annotations

import pytest

from src.domain.user_profile import ProfileLink, UserProfile
from src.infra.memory.mem_user_profile_repo import InMemoryUserProfileRepository


@pytest.mark.asyncio
class TestUserProfileRepo:
    async def test_get_missing_returns_none(self):
        repo = InMemoryUserProfileRepository()
        assert await repo.get("nope") is None

    async def test_upsert_then_get_roundtrip(self):
        repo = InMemoryUserProfileRepository()
        await repo.upsert(UserProfile(
            user_id="u1", summary="hi", links=[ProfileLink("Site", "https://x.io")]))
        got = await repo.get("u1")
        assert got.summary == "hi"
        assert got.links[0].name == "Site"
        assert got.links[0].url == "https://x.io"

    async def test_upsert_replaces_prior(self):
        repo = InMemoryUserProfileRepository()
        await repo.upsert(UserProfile(user_id="u1", summary="a", links=[]))
        await repo.upsert(UserProfile(
            user_id="u1", summary="b", links=[ProfileLink("N", "https://y.io")]))
        got = await repo.get("u1")
        assert got.summary == "b"
        assert len(got.links) == 1
