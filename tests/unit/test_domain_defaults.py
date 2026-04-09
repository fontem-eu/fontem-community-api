"""Test that domain dataclass defaults are correct — kills mutmut mutations on default values."""
from __future__ import annotations

from src.domain.issue import Issue, Comment
from src.domain.moderation import Flag, Sanction
from src.domain.report import Report, Section, SectionVersion, AccessGrant
from src.domain.group import Group
from src.domain.user import User


class TestIssueDefaults:
    def test_id(self):
        assert Issue().id is None

    def test_title(self):
        assert Issue().title == ""

    def test_body_md(self):
        assert Issue().body_md == ""

    def test_issue_type(self):
        assert Issue().issue_type == "other"

    def test_entity_type(self):
        assert Issue().entity_type == ""

    def test_entity_id(self):
        assert Issue().entity_id == ""

    def test_status(self):
        assert Issue().status == "open"

    def test_created_by(self):
        assert Issue().created_by == ""

    def test_created_at(self):
        assert Issue().created_at is None

    def test_updated_at(self):
        assert Issue().updated_at is None


class TestCommentDefaults:
    def test_id(self):
        assert Comment().id is None

    def test_parent_type(self):
        assert Comment().parent_type == ""

    def test_parent_id(self):
        assert Comment().parent_id == ""

    def test_body_md(self):
        assert Comment().body_md == ""

    def test_author_id(self):
        assert Comment().author_id == ""

    def test_created_at(self):
        assert Comment().created_at is None


class TestFlagDefaults:
    def test_id(self):
        assert Flag().id is None

    def test_target_type(self):
        assert Flag().target_type == ""

    def test_target_id(self):
        assert Flag().target_id == ""

    def test_reason(self):
        assert Flag().reason == "other"

    def test_details(self):
        assert Flag().details == ""

    def test_flagged_by(self):
        assert Flag().flagged_by == ""

    def test_created_at(self):
        assert Flag().created_at is None


class TestSanctionDefaults:
    def test_id(self):
        assert Sanction().id is None

    def test_user_id(self):
        assert Sanction().user_id == ""

    def test_type(self):
        assert Sanction().type == "warning"

    def test_reason(self):
        assert Sanction().reason == ""

    def test_starts_at(self):
        assert Sanction().starts_at is None

    def test_expires_at(self):
        assert Sanction().expires_at is None

    def test_applied_by(self):
        assert Sanction().applied_by == ""

    def test_lifted_at(self):
        assert Sanction().lifted_at is None


class TestReportDefaults:
    def test_id(self):
        assert Report().id is None

    def test_title(self):
        assert Report().title == ""

    def test_abstract(self):
        assert Report().abstract is None

    def test_visibility(self):
        assert Report().visibility == "private"

    def test_parent_id(self):
        assert Report().parent_id is None

    def test_created_by(self):
        assert Report().created_by == ""

    def test_created_at(self):
        assert Report().created_at is None

    def test_updated_at(self):
        assert Report().updated_at is None


class TestSectionDefaults:
    def test_id(self):
        assert Section().id is None

    def test_report_id(self):
        assert Section().report_id == ""

    def test_sort_order(self):
        assert Section().sort_order == 0

    def test_content_json(self):
        assert Section().content_json == {}

    def test_content_json_is_fresh_dict(self):
        s1 = Section()
        s2 = Section()
        assert s1.content_json is not s2.content_json

    def test_lock_holder(self):
        assert Section().lock_holder is None

    def test_lock_expires(self):
        assert Section().lock_expires is None

    def test_updated_at(self):
        assert Section().updated_at is None


class TestSectionVersionDefaults:
    def test_id(self):
        assert SectionVersion().id is None

    def test_section_id(self):
        assert SectionVersion().section_id == ""

    def test_content_json(self):
        assert SectionVersion().content_json == {}

    def test_saved_by(self):
        assert SectionVersion().saved_by == ""

    def test_saved_at(self):
        assert SectionVersion().saved_at is None


class TestAccessGrantDefaults:
    def test_id(self):
        assert AccessGrant().id is None

    def test_report_id(self):
        assert AccessGrant().report_id == ""

    def test_user_id(self):
        assert AccessGrant().user_id is None

    def test_group_id(self):
        assert AccessGrant().group_id is None

    def test_level(self):
        assert AccessGrant().level == "viewer"


class TestGroupDefaults:
    def test_id(self):
        assert Group().id is None

    def test_name(self):
        assert Group().name == ""

    def test_description(self):
        assert Group().description == ""

    def test_created_at(self):
        assert Group().created_at is None


class TestUserDefaults:
    def test_id(self):
        assert User().id is None

    def test_email(self):
        assert User().email == ""

    def test_name(self):
        assert User().name == ""

    def test_avatar_url(self):
        assert User().avatar_url is None

    def test_trust_level(self):
        assert User().trust_level == "new_user"

    def test_created_at(self):
        assert User().created_at is None
