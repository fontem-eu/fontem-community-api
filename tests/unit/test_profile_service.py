"""ProfileService: composes identity + extras + public articles + activity,
and validates profile updates."""
from __future__ import annotations

import pytest

from src.domain.activity import ActivityEvent
from src.domain.report import Report
from src.domain.user_profile import ProfileLink, UserProfile
from src.infra.memory.mem_activity_repo import InMemoryActivityRepository
from src.infra.memory.mem_report_repo import InMemoryReportRepository
from src.infra.memory.mem_user_profile_repo import InMemoryUserProfileRepository
from src.infra.memory.mem_user_repo import InMemoryUserRepository
from src.services.exceptions import NotFound
from src.services.profile_service import ProfileService
from tests.conftest import seed_user


async def _make():
    users = InMemoryUserRepository()
    profiles = InMemoryUserProfileRepository()
    reports = InMemoryReportRepository()
    activity = InMemoryActivityRepository()
    svc = ProfileService(users=users, profiles=profiles, reports=reports, activity=activity)
    return svc, users, profiles, reports, activity


@pytest.mark.asyncio
class TestProfileService:
    async def test_missing_user_raises(self):
        svc, *_ = await _make()
        with pytest.raises(NotFound):
            await svc.get_profile("ghost", viewer_authed=False)

    async def test_composes_only_own_public_articles_plus_activity(self):
        svc, users, profiles, reports, activity = await _make()
        u = await seed_user(users, "u1")
        other = await seed_user(users, "u2")
        await profiles.upsert(UserProfile(
            user_id=u.id, summary="Investigator.",
            links=[ProfileLink("Mastodon", "https://m.io/x")]))
        await reports.create(Report(id="a1", title="Public one",
                                    visibility="public_open", created_by=u.id))
        await reports.create(Report(id="a2", title="Private",
                                    visibility="private", created_by=u.id))
        await reports.create(Report(id="a3", title="Others",
                                    visibility="public_open", created_by=other.id))
        await activity.record(ActivityEvent(
            actor_id=u.id, entity_type="story", entity_id="a1",
            action="created", summary="Public one"))

        prof = await svc.get_profile(u.id, viewer_authed=False)
        assert prof["name"] == u.name
        assert prof["summary"] == "Investigator."
        assert prof["links"] == [{"name": "Mastodon", "url": "https://m.io/x"}]
        assert [a["title"] for a in prof["articles"]] == ["Public one"]
        assert prof["recent_activity"][0]["entity_id"] == "a1"

    async def test_public_auth_visible_only_to_authed_viewer(self):
        svc, users, _, reports, _ = await _make()
        u = await seed_user(users, "u1")
        await reports.create(Report(id="a1", title="Open",
                                    visibility="public_open", created_by=u.id))
        await reports.create(Report(id="a2", title="AuthOnly",
                                    visibility="public_auth", created_by=u.id))
        anon = await svc.get_profile(u.id, viewer_authed=False)
        authed = await svc.get_profile(u.id, viewer_authed=True)
        assert {a["title"] for a in anon["articles"]} == {"Open"}
        assert {a["title"] for a in authed["articles"]} == {"Open", "AuthOnly"}

    async def test_update_validates_and_persists_links(self):
        svc, users, *_ = await _make()
        u = await seed_user(users, "u1")
        result = await svc.update_own_profile(
            u.id,
            "  My bio  ",
            [
                {"name": "Good", "url": "https://ok.io"},
                {"name": "", "url": "https://noname.io"},            # no name
                {"name": "NoUrl", "url": ""},                        # no url
                {"name": "Bad", "url": "javascript:alert(1)"},       # bad scheme
            ],
        )
        assert result["summary"] == "My bio"
        assert result["links"] == [{"name": "Good", "url": "https://ok.io"}]
        prof = await svc.get_profile(u.id, viewer_authed=False)
        assert prof["links"] == [{"name": "Good", "url": "https://ok.io"}]
