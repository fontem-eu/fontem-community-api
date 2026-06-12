"""Unit tests for the AuthorizationService + policy.

Three layers of coverage:

1. **Policy matrix** — every Action × happy + at least one deny path.
   The policy table is the load-bearing thing; if the matrix is
   wrong, every router downstream is wrong. Tests are tiny (one
   policy call each) so adding a new Action means adding two test
   rows here.
2. **Service integration** — ``AuthorizationService.require`` raises
   on deny, ``decide`` returns + audits both ways, ``principal``
   builds the snapshot.
3. **Audit log** — every decide call lands a row in the audit
   repo with the right shape.

The fixtures use the in-memory user repo + the in-memory audit repo
so the tests run in microseconds without a database. Nothing here
mocks; everything is real wiring with the in-memory backends the
production code already provides.
"""
# pylint: disable=protected-access
from __future__ import annotations

import pytest

from src.domain.group import Group
from src.domain.report import Report
from src.domain.user import User
from src.infra.memory.mem_authz_audit_repo import InMemoryAuthzAuditRepository
from src.infra.memory.mem_user_repo import InMemoryUserRepository
from src.services.authz import (
    Action,
    AuthorizationService,
    Decision,
    Principal,
    ResourceRef,
)
from src.services.authz.audit import AuditLogger
from src.services.authz.policy import evaluate
from src.services.exceptions import PermissionDenied


# ── Fixtures ──────────────────────────────────────────────────


def _principal(
    *,
    user_id: str = "alice",
    trust_level: str = "new_user",
    roles: frozenset[str] = frozenset(),
    sanction: str | None = None,
) -> Principal:
    return Principal(
        user_id=user_id, trust_level=trust_level, roles=roles, sanction=sanction,
    )


def _group(owner_id: str | None = "alice", id_: str = "g1") -> ResourceRef:
    return ResourceRef.for_group(
        Group(id=id_, name="x", description="", created_by=owner_id),
    )


def _story(
    *, owner_id: str | None = "alice", visibility: str = "private", id_: str = "s1",
) -> ResourceRef:
    return ResourceRef.for_story(
        Report(id=id_, title="x", abstract="x", visibility=visibility, created_by=owner_id),
    )


# ── Policy matrix — one happy + one deny per Action ──────────


class TestPolicyMatrix:
    """One representative test per Action. Adding a new Action without
    a matrix row here will leave the policy un-pinned."""

    def test_users_read_self_owner(self):
        p = _principal(user_id="alice")
        r = ResourceRef(kind="user", id="alice")
        assert evaluate(p, Action.USERS_READ_SELF, r).allowed

    def test_users_read_self_blocks_other(self):
        p = _principal(user_id="alice")
        r = ResourceRef(kind="user", id="bob")
        assert not evaluate(p, Action.USERS_READ_SELF, r).allowed

    def test_users_read_public_allows_anyone(self):
        p = _principal(user_id="alice")
        r = ResourceRef(kind="user", id="bob")
        assert evaluate(p, Action.USERS_READ_PUBLIC, r).allowed

    def test_stories_read_public_open(self):
        p = _principal(user_id="anyone")
        assert evaluate(p, Action.STORIES_READ, _story(owner_id="alice", visibility="public_open")).allowed

    def test_stories_read_private_blocks_non_owner(self):
        p = _principal(user_id="bob")
        assert not evaluate(p, Action.STORIES_READ, _story(owner_id="alice", visibility="private")).allowed

    def test_stories_edit_owner(self):
        p = _principal(user_id="alice")
        assert evaluate(p, Action.STORIES_EDIT, _story(owner_id="alice")).allowed

    def test_stories_edit_blocks_non_owner(self):
        p = _principal(user_id="bob")
        assert not evaluate(p, Action.STORIES_EDIT, _story(owner_id="alice")).allowed

    def test_groups_create_new_user_allowed(self):
        # Group creation is cheap; new_user can create. The 50-cap on
        # flowers + tags handles spam at a different layer.
        p = _principal(trust_level="new_user")
        assert evaluate(p, Action.GROUPS_CREATE, None).allowed

    def test_groups_manage_members_owner_allowed(self):
        p = _principal(user_id="alice")
        assert evaluate(p, Action.GROUPS_MANAGE_MEMBERS, _group(owner_id="alice")).allowed

    def test_groups_manage_members_blocks_non_owner(self):
        # This is the regression. Mallory is not the owner — deny.
        p = _principal(user_id="mallory")
        verdict = evaluate(p, Action.GROUPS_MANAGE_MEMBERS, _group(owner_id="alice"))
        assert not verdict.allowed
        assert "not owner" in verdict.reason

    def test_groups_read_members_blocks_non_owner(self):
        # The membership list itself is owner-only.
        p = _principal(user_id="mallory")
        assert not evaluate(p, Action.GROUPS_READ_MEMBERS, _group(owner_id="alice")).allowed

    def test_groups_manage_members_legacy_null_owner_blocked(self):
        # A group with no created_by (pre-authz-service row) is admin-only.
        p = _principal(user_id="alice")
        assert not evaluate(p, Action.GROUPS_MANAGE_MEMBERS, _group(owner_id=None)).allowed

    def test_groups_manage_members_admin_override(self):
        p = _principal(user_id="root", trust_level="admin")
        assert evaluate(p, Action.GROUPS_MANAGE_MEMBERS, _group(owner_id="alice")).allowed

    def test_issues_create_requires_contributor(self):
        assert not evaluate(_principal(trust_level="new_user"), Action.ISSUES_CREATE, None).allowed
        assert evaluate(_principal(trust_level="contributor"), Action.ISSUES_CREATE, None).allowed

    def test_issues_comment_blocked_by_mute(self):
        p = _principal(trust_level="commenter", sanction="mute")
        assert not evaluate(p, Action.ISSUES_COMMENT, None).allowed

    def test_flags_resolve_requires_moderator(self):
        assert not evaluate(_principal(trust_level="contributor"), Action.FLAGS_RESOLVE, None).allowed
        assert evaluate(_principal(trust_level="moderator"), Action.FLAGS_RESOLVE, None).allowed

    def test_sanctions_revoke_requires_admin(self):
        assert not evaluate(_principal(trust_level="moderator"), Action.SANCTIONS_REVOKE, None).allowed
        assert evaluate(_principal(trust_level="admin"), Action.SANCTIONS_REVOKE, None).allowed


class TestSanctions:
    """Sanctions short-circuit before per-action checks."""

    def test_ban_blocks_everything(self):
        p = _principal(trust_level="admin", sanction="ban")
        # Even admin can't act while banned — the ban is platform-level.
        assert not evaluate(p, Action.STORIES_READ, _story(visibility="public_open")).allowed
        assert not evaluate(p, Action.STORIES_CREATE, None).allowed

    def test_suspend_allows_read_only(self):
        p = _principal(trust_level="contributor", sanction="suspend")
        # Read-only actions still go through.
        assert evaluate(p, Action.STORIES_READ, _story(visibility="public_open")).allowed
        assert evaluate(p, Action.USERS_READ_SELF, ResourceRef(kind="user", id=p.user_id)).allowed
        # State-mutating ones don't.
        assert not evaluate(p, Action.STORIES_CREATE, None).allowed
        assert not evaluate(p, Action.GROUPS_CREATE, None).allowed


# ── Service integration ──────────────────────────────────────


@pytest.fixture
def authz_setup():
    users = InMemoryUserRepository()
    audit = InMemoryAuthzAuditRepository()
    svc = AuthorizationService(users=users, audit=AuditLogger(audit))
    return svc, users, audit


class TestAuthorizationService:
    @pytest.mark.asyncio
    async def test_principal_returns_none_for_anon(self, authz_setup):
        svc, _, _ = authz_setup
        assert (await svc.principal(None)) is None

    @pytest.mark.asyncio
    async def test_principal_snapshots_user(self, authz_setup):
        svc, users, _ = authz_setup
        u = User(id="alice", email="a@x", name="Alice", trust_level="contributor")
        await users.upsert(u)
        p = await svc.principal("alice")
        assert p is not None
        assert p.user_id == "alice"
        assert p.trust_level == "contributor"
        assert p.sanction is None

    @pytest.mark.asyncio
    async def test_require_raises_on_deny(self, authz_setup):
        svc, users, _ = authz_setup
        u = User(id="bob", email="b@x", name="Bob", trust_level="new_user")
        await users.upsert(u)
        p = await svc.principal("bob")
        with pytest.raises(PermissionDenied):
            await svc.require(p, Action.GROUPS_MANAGE_MEMBERS, _group(owner_id="alice"))

    @pytest.mark.asyncio
    async def test_require_silent_on_allow(self, authz_setup):
        svc, users, _ = authz_setup
        u = User(id="alice", email="a@x", name="Alice", trust_level="new_user")
        await users.upsert(u)
        p = await svc.principal("alice")
        # No exception → allowed.
        await svc.require(p, Action.GROUPS_MANAGE_MEMBERS, _group(owner_id="alice"))

    @pytest.mark.asyncio
    async def test_decide_logs_both_outcomes(self, authz_setup):
        svc, users, audit = authz_setup
        u = User(id="alice", email="a@x", name="Alice", trust_level="new_user")
        await users.upsert(u)
        p = await svc.principal("alice")
        await svc.decide(p, Action.GROUPS_MANAGE_MEMBERS, _group(owner_id="alice"))
        await svc.decide(p, Action.GROUPS_MANAGE_MEMBERS, _group(owner_id="mallory"))

        assert len(audit.rows) == 2
        allow_row, deny_row = audit.rows
        assert allow_row.action == "groups:manage_members"
        assert allow_row.allowed is True
        assert allow_row.reason == "owner"
        assert deny_row.allowed is False
        assert "not owner" in deny_row.reason

    @pytest.mark.asyncio
    async def test_anon_principal_decision_is_denied_and_logged(self, authz_setup):
        svc, _, audit = authz_setup
        decision = await svc.decide(None, Action.STORIES_CREATE, None)
        assert not decision.allowed
        assert decision.reason == "unauthenticated"
        assert len(audit.rows) == 1
        assert audit.rows[0].user_id is None


# ── Decision helpers ──────────────────────────────────────────


class TestDecisionHelpers:
    def test_allow_and_deny_constructors(self):
        a = Decision.allow("owner")
        d = Decision.deny("not owner")
        assert a.allowed and not d.allowed
        assert a.reason == "owner" and d.reason == "not owner"
