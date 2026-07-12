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
from src.services.profile_service import ProfileService, _valid_email
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
                {"name": "Schemeless", "url": "linkedin.com/in/x"},  # normalised
                {"name": "Http", "url": "http://legacy.io"},         # upgraded
                {"name": "", "url": "https://noname.io"},            # no name
                {"name": "NoUrl", "url": ""},                        # no url
                {"name": "Bad", "url": "javascript:alert(1)"},       # rejected
            ],
        )
        assert result["summary"] == "My bio"
        assert result["links"] == [
            {"name": "Good", "url": "https://ok.io"},
            {"name": "Schemeless", "url": "https://linkedin.com/in/x"},
            {"name": "Http", "url": "https://legacy.io"},
        ]
        prof = await svc.get_profile(u.id, viewer_authed=False)
        assert [l["url"] for l in prof["links"]] == [
            "https://ok.io", "https://linkedin.com/in/x", "https://legacy.io",
        ]

    async def test_avatar_focal_point_persists_and_clamps(self):
        svc, users, *_ = await _make()
        u = await seed_user(users, "u1")
        r = await svc.update_own_profile(u.id, "", [], avatar_x=25, avatar_y=150)
        assert r["avatar_x"] == 25
        assert r["avatar_y"] == 100  # clamped to [0, 100]
        # a summary-only save preserves the focal point
        await svc.update_own_profile(u.id, "hi", [])
        prof = await svc.get_profile(u.id, viewer_authed=False)
        assert prof["avatar_x"] == 25 and prof["avatar_y"] == 100

    async def test_name_change_and_email_settings(self):
        svc, users, *_ = await _make()
        u = await seed_user(users, "u1")
        # set a display email (account email), a custom one, and rename
        r = await svc.update_own_profile(
            u.id, "", [], name="  New Name  ",
            show_email=True, use_custom_email=True, custom_email="hi@x.io")
        assert r["name"] == "New Name"
        assert r["show_email"] is True and r["use_custom_email"] is True
        assert r["custom_email"] == "hi@x.io"
        # invalid custom email -> use_custom falls back off
        r2 = await svc.update_own_profile(u.id, "", [], custom_email="not-an-email")
        assert r2["use_custom_email"] is False and r2["custom_email"] == ""
        # owner GET exposes settings; public GET only the resolved email
        owner = await svc.get_profile(u.id, viewer_authed=True, caller_id=u.id)
        assert owner["show_email"] is True and owner["account_email"] == u.email
        # after r2 the custom email is gone -> public email is the account one
        assert owner["email"] == u.email
        public = await svc.get_profile(u.id, viewer_authed=False, caller_id=None)
        assert "account_email" not in public and "custom_email" not in public
        assert public["email"] == u.email

    async def test_email_hidden_when_show_email_off(self):
        svc, users, *_ = await _make()
        u = await seed_user(users, "u1")
        await svc.update_own_profile(u.id, "", [], show_email=False)
        prof = await svc.get_profile(u.id, viewer_authed=False)
        assert prof["email"] == ""

    async def test_home_nuts_set_validate_clear_and_partial_update(self):
        svc, users, _profiles, _reports, _activity = await _make()
        u = await seed_user(users, "u1")
        # a valid NUTS-3 code (lowercased input is normalised to upper)
        r = await svc.update_own_profile(u.id, "", [], home_nuts="pt170")
        assert r["home_nuts"] == "PT170"
        # a partial update (home_nuts=None) leaves it intact
        r = await svc.update_own_profile(u.id, "new bio", [])
        assert r["home_nuts"] == "PT170"
        # a malformed code is ignored (keeps the good one)
        r = await svc.update_own_profile(u.id, "", [], home_nuts="not a nuts!!")
        assert r["home_nuts"] == "PT170"
        # empty string clears it
        r = await svc.update_own_profile(u.id, "", [], home_nuts="")
        assert r["home_nuts"] == ""

    async def test_home_nuts_is_public_on_the_profile(self):
        svc, users, _profiles, _reports, _activity = await _make()
        u = await seed_user(users, "u1")
        await svc.update_own_profile(u.id, "", [], home_nuts="DE21")
        prof = await svc.get_profile(u.id, viewer_authed=False, caller_id=None)
        assert prof["home_nuts"] == "DE21"


def test_valid_email_rejects_control_chars_for_mailto_safety():
    """A custom email must not smuggle CRLF/tabs into the rendered mailto link."""
    assert _valid_email("person@example.com")
    for bad in ("a@b.com\r\nbcc:x@y.com", "a@b.com\tx", "a@b.com y",
                "a@b\u00a0.com", "x@y", "@y.com", "a@.com"):
        assert not _valid_email(bad), bad
