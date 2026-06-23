"""Policy matrix for investigations:* actions over the linear role hierarchy
viewer < contributor < admin < owner. Pure-function tests — no DB, no service.
"""
from __future__ import annotations

import pytest

from src.domain.investigation import Investigation, InvestigationMember
from src.services.authz import Action, Principal, ResourceRef
from src.services.authz.policy import evaluate

INV = Investigation(id="inv-1", name="X", created_by="creator")


def _p(uid="u", trust="contributor", roles=frozenset(), verified=True):
    return Principal(user_id=uid, trust_level=trust, roles=roles, email_verified=verified)


def _member(uid, role="viewer"):
    return InvestigationMember("inv-1", uid, role=role)


# ── CREATE ──
def test_create_allowed_for_verified_user():
    assert evaluate(_p("u"), Action.INVESTIGATIONS_CREATE, None).allowed


def test_create_denied_for_unverified():
    assert not evaluate(_p("u", verified=False), Action.INVESTIGATIONS_CREATE, None).allowed


# ── READ (any member) ──
def test_read_viewer_allowed():
    r = ResourceRef.for_investigation(INV, _member("m", "viewer"))
    assert evaluate(_p("m"), Action.INVESTIGATIONS_READ, r).allowed


def test_read_nonmember_denied():
    r = ResourceRef.for_investigation(INV, None)
    assert not evaluate(_p("x"), Action.INVESTIGATIONS_READ, r).allowed


def test_read_creator_allowed():
    r = ResourceRef.for_investigation(INV, None)
    assert evaluate(_p("creator"), Action.INVESTIGATIONS_READ, r).allowed


def test_read_admin_override_allowed():
    r = ResourceRef.for_investigation(INV, None)
    assert evaluate(_p("a", roles=frozenset({"admin"})), Action.INVESTIGATIONS_READ, r).allowed


# ── role thresholds: (action, minimum role that unlocks it) ──
ACTION_MIN_ROLE = [
    (Action.INVESTIGATIONS_ADD_STORY, "contributor"),
    (Action.INVESTIGATIONS_REMOVE_STORY, "contributor"),
    (Action.INVESTIGATIONS_ADD_VIZ, "contributor"),
    (Action.INVESTIGATIONS_REMOVE_VIZ, "contributor"),
    (Action.INVESTIGATIONS_EDIT_META, "admin"),
    (Action.INVESTIGATIONS_MANAGE_MEMBERS, "admin"),
    (Action.INVESTIGATIONS_DELETE, "owner"),
]
RANK = {"viewer": 0, "contributor": 1, "admin": 2, "owner": 3}
ALL_ROLES = ["viewer", "contributor", "admin", "owner"]
ACTIONS = [a for a, _ in ACTION_MIN_ROLE]


@pytest.mark.parametrize("action,min_role", ACTION_MIN_ROLE)
@pytest.mark.parametrize("role", ALL_ROLES)
def test_role_threshold_matrix(action, min_role, role):
    """Every (role x action) cell: allowed iff role rank >= the action's minimum."""
    r = ResourceRef.for_investigation(INV, _member("m", role))
    expected = RANK[role] >= RANK[min_role]
    assert evaluate(_p("m"), action, r).allowed is expected


@pytest.mark.parametrize("action", ACTIONS)
def test_nonmember_denied(action):
    r = ResourceRef.for_investigation(INV, None)
    assert not evaluate(_p("x"), action, r).allowed


@pytest.mark.parametrize("action", ACTIONS)
def test_creator_behaves_as_owner(action):
    r = ResourceRef.for_investigation(INV, None)
    assert evaluate(_p("creator"), action, r).allowed


# ── escalation spot-checks ──
def test_contributor_cannot_manage_members():
    r = ResourceRef.for_investigation(INV, _member("m", "contributor"))
    assert not evaluate(_p("m"), Action.INVESTIGATIONS_MANAGE_MEMBERS, r).allowed


def test_admin_cannot_delete():
    r = ResourceRef.for_investigation(INV, _member("m", "admin"))
    assert not evaluate(_p("m"), Action.INVESTIGATIONS_DELETE, r).allowed


def test_viewer_cannot_add_story():
    r = ResourceRef.for_investigation(INV, _member("m", "viewer"))
    assert not evaluate(_p("m"), Action.INVESTIGATIONS_ADD_STORY, r).allowed
