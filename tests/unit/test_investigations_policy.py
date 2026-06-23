"""Policy matrix for investigations:* actions (M1).

Pure-function policy tests — one assertion per (capability x action) cell,
plus the owner / non-member / admin paths. No DB, no service.
"""
from __future__ import annotations

import pytest

from src.domain.investigation import Investigation, InvestigationMember
from src.services.authz import Action, Principal, ResourceRef
from src.services.authz.policy import evaluate

INV = Investigation(id="inv-1", name="X", created_by="creator")


def _p(uid="u", trust="contributor", roles=frozenset(), verified=True):
    return Principal(user_id=uid, trust_level=trust, roles=roles, email_verified=verified)


def _member(uid, write=False, viz=False, admin=False, owner=False):
    return InvestigationMember(
        "inv-1", uid,
        can_write_stories=write, can_add_viz=viz, can_administer=admin, is_owner=owner,
    )


# ── CREATE ──
def test_create_allowed_for_verified_user():
    assert evaluate(_p("u"), Action.INVESTIGATIONS_CREATE, None).allowed


def test_create_denied_for_unverified():
    assert not evaluate(_p("u", verified=False), Action.INVESTIGATIONS_CREATE, None).allowed


# ── READ (member-only) ──
def test_read_member_allowed():
    r = ResourceRef.for_investigation(INV, _member("m"))
    assert evaluate(_p("m"), Action.INVESTIGATIONS_READ, r).allowed


def test_read_nonmember_denied():
    r = ResourceRef.for_investigation(INV, None)
    assert not evaluate(_p("x"), Action.INVESTIGATIONS_READ, r).allowed


def test_read_creator_allowed():
    r = ResourceRef.for_investigation(INV, None)
    assert evaluate(_p("creator"), Action.INVESTIGATIONS_READ, r).allowed


def test_read_admin_allowed():
    r = ResourceRef.for_investigation(INV, None)
    assert evaluate(_p("a", roles=frozenset({"admin"})), Action.INVESTIGATIONS_READ, r).allowed


# ── capability-gated verbs ──
CAP_ACTIONS = [
    (Action.INVESTIGATIONS_EDIT_META, "admin"),
    (Action.INVESTIGATIONS_MANAGE_MEMBERS, "admin"),
    (Action.INVESTIGATIONS_ADD_STORY, "write"),
    (Action.INVESTIGATIONS_REMOVE_STORY, "write"),
    (Action.INVESTIGATIONS_ADD_VIZ, "viz"),
    (Action.INVESTIGATIONS_REMOVE_VIZ, "viz"),
]

CAP_ACTION_NAMES = [a for a, _ in CAP_ACTIONS]


@pytest.mark.parametrize("action,cap", CAP_ACTIONS)
def test_capability_allows_its_action(action, cap):
    r = ResourceRef.for_investigation(INV, _member("m", **{cap: True}))
    assert evaluate(_p("m"), action, r).allowed


@pytest.mark.parametrize("action", CAP_ACTION_NAMES)
def test_member_without_capability_denied(action):
    r = ResourceRef.for_investigation(INV, _member("m"))  # plain member, no caps
    assert not evaluate(_p("m"), action, r).allowed


@pytest.mark.parametrize("action", CAP_ACTION_NAMES)
def test_owner_holds_every_capability(action):
    r = ResourceRef.for_investigation(INV, _member("m", owner=True))
    assert evaluate(_p("m"), action, r).allowed


@pytest.mark.parametrize("action", CAP_ACTION_NAMES)
def test_nonmember_denied(action):
    r = ResourceRef.for_investigation(INV, None)
    assert not evaluate(_p("x"), action, r).allowed


# ── DELETE (owner-only) ──
def test_delete_owner_allowed():
    r = ResourceRef.for_investigation(INV, _member("m", owner=True))
    assert evaluate(_p("m"), Action.INVESTIGATIONS_DELETE, r).allowed


def test_delete_administer_only_denied():
    # can_administer but not owner -> cannot delete
    r = ResourceRef.for_investigation(INV, _member("m", admin=True))
    assert not evaluate(_p("m"), Action.INVESTIGATIONS_DELETE, r).allowed


def test_delete_creator_allowed():
    r = ResourceRef.for_investigation(INV, None)
    assert evaluate(_p("creator"), Action.INVESTIGATIONS_DELETE, r).allowed


def test_delete_nonmember_denied():
    r = ResourceRef.for_investigation(INV, None)
    assert not evaluate(_p("x"), Action.INVESTIGATIONS_DELETE, r).allowed
