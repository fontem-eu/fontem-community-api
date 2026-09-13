"""The admin user directory: who may read it, and what a row says.

The directory is every account's email beside its sign-ins and activity, so
most of this file is about refusal — anonymous callers, every trust level
short of admin, a moderator, a banned admin, an admin whose email is not
confirmed — and about what never appears in a row however it is asked for.
"""
# pylint: disable=missing-function-docstring,redefined-outer-name
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import pytest

from src.domain.activity import ActivityEvent
from src.domain.moderation import Sanction
from src.domain.user import User
from src.repositories.refresh_token_repository import RefreshTokenFamily
from src.services.exceptions import InvalidInput, PermissionDenied
from tests.conftest import make_headers, seed_user

ADMIN = "directory-admin"
ACTION = "admin:users_list"
NOW = datetime.now(timezone.utc)

ROW_FIELDS = {
    "id", "email", "name", "trust_level", "roles", "email_verified",
    "registered_at", "last_login_at", "last_seen_at", "last_activity_at",
    "activity_count",
}


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _directory(client, who=ADMIN, **params):
    return client.get("/admin/users", params=params, headers=make_headers(who))


def _audit_rows(services):
    return [row for row in services["authz_audit_repo"].rows if row.action == ACTION]


def _row_for(body, email):
    return next(user for user in body["users"] if user["email"] == email)


def _at(value):
    return datetime.fromisoformat(value) if value else None


@pytest.fixture()
def admin(services):
    return _run(seed_user(services["user_repo"], ADMIN, trust_level="admin"))


# ── Who may read it ──────────────────────────────────────────────────────

def test_it_requires_authentication(client, services):
    assert client.get("/admin/users").status_code in (401, 403)
    # Refused before the service is reached, so there is nobody to audit.
    assert _audit_rows(services) == []


@pytest.mark.parametrize("level", ["new_user", "commenter", "contributor", "moderator"])
def test_every_trust_level_short_of_admin_is_refused_and_audited(client, services, level):
    label = f"directory-{level}"
    user = _run(seed_user(services["user_repo"], label, trust_level=level))

    assert _directory(client, who=label).status_code == 403
    assert [(row.user_id, row.allowed) for row in _audit_rows(services)] == [(user.id, False)]


def test_a_moderator_role_is_not_enough(client, services):
    # Moderation does not need everyone's email address.
    label = "directory-moderator-role"
    _run(seed_user(services["user_repo"], label, trust_level="contributor", roles=["moderator"]))
    assert _directory(client, who=label).status_code == 403


def test_an_explicit_admin_role_is_enough(client, services):
    label = "directory-admin-role"
    _run(seed_user(services["user_repo"], label, trust_level="contributor", roles=["admin"]))
    assert _directory(client, who=label).status_code == 200


def test_an_admin_reads_it_and_the_read_is_audited(client, services, admin):
    assert _directory(client).status_code == 200
    assert [(row.user_id, row.allowed) for row in _audit_rows(services)] == [(admin.id, True)]


def test_a_banned_admin_is_refused(client, services, admin):
    # Sanctions are checked before roles: a ban is not escaped by being admin.
    _run(services["user_repo"].add_sanction(Sanction(
        user_id=admin.id, type="ban", reason="test",
        starts_at=NOW - timedelta(hours=1), applied_by=admin.id)))
    assert _directory(client).status_code in (401, 403)


def test_an_admin_with_an_unconfirmed_email_is_refused(client, services):
    label = "directory-unverified"
    _run(seed_user(services["user_repo"], label, trust_level="admin", email_verified=False))
    assert _directory(client, who=label).status_code == 403


def test_the_service_refuses_before_it_judges_the_arguments(services):
    # A caller who may not read the directory learns nothing from how their
    # arguments would have been judged.
    user = _run(seed_user(services["user_repo"], "directory-probe", trust_level="contributor"))
    with pytest.raises(PermissionDenied):
        _run(services["user_directory_svc"].list_users(user.id, limit=10_000))


def test_the_service_bounds_its_arguments_for_callers_that_skip_the_router(services, admin):
    with pytest.raises(InvalidInput):
        _run(services["user_directory_svc"].list_users(admin.id, limit=10_000))
    with pytest.raises(InvalidInput):
        _run(services["user_directory_svc"].list_users(admin.id, sort="email"))


# ── What a row says ──────────────────────────────────────────────────────

@pytest.mark.usefixtures("admin")
def test_a_row_carries_the_directory_fields_and_nothing_else(client, services):
    _run(services["user_repo"].upsert(User(
        id=str(uuid.uuid4()), email="local-account@test.com", name="P",
        password_hash=bcrypt.hashpw(b"correct horse", bcrypt.gensalt()).decode(),
        failed_login_attempts=4)))

    resp = _directory(client)

    assert resp.status_code == 200
    for row in resp.json()["users"]:
        assert set(row) == ROW_FIELDS
    # The account with a password is in the directory…
    assert _row_for(resp.json(), "local-account@test.com")
    # …and however a row is built, no credential material reaches the response.
    assert "$2b$" not in resp.text
    assert "password_hash" not in resp.text
    assert "failed_login" not in resp.text


def test_activity_is_counted_and_dated_per_account(client, services, admin):
    author = _run(seed_user(services["user_repo"], "directory-author"))
    times = [NOW - timedelta(days=days) for days in (5, 3, 2)]
    for when in times:
        _run(services["activity_repo"].record(ActivityEvent(
            actor_id=author.id, entity_type="story", entity_id="s",
            action="updated", summary="s", created_at=when)))

    body = _directory(client).json()

    assert _row_for(body, author.email)["activity_count"] == 3
    assert _at(_row_for(body, author.email)["last_activity_at"]) == max(times)
    assert _row_for(body, admin.email)["activity_count"] == 0
    assert _row_for(body, admin.email)["last_activity_at"] is None


@pytest.mark.usefixtures("admin")
def test_last_seen_is_the_latest_session_refresh(client, services):
    reader = _run(seed_user(services["user_repo"], "directory-reader"))
    for hours in (30, 2):
        _run(services["refresh_token_repo"].create_family(RefreshTokenFamily(
            id=str(uuid.uuid4()), user_id=reader.id, current_token_hash=uuid.uuid4().hex,
            rotated_at=NOW - timedelta(hours=hours), expires_at=NOW + timedelta(days=14))))

    body = _directory(client).json()

    assert _at(_row_for(body, reader.email)["last_seen_at"]) == NOW - timedelta(hours=2)


@pytest.mark.usefixtures("admin")
def test_signing_in_records_last_login_and_a_failed_attempt_does_not(client, services):
    user_repo = services["user_repo"]
    uid = str(uuid.uuid4())
    _run(user_repo.upsert(User(
        id=uid, email="signs-in@test.com", name="S",
        password_hash=bcrypt.hashpw(b"right-password", bcrypt.gensalt()).decode())))

    wrong = client.post("/auth/login", json={"email": "signs-in@test.com", "password": "nope-nope"})
    assert wrong.status_code == 401
    assert _run(user_repo.get_by_id(uid)).last_login_at is None

    before = datetime.now(timezone.utc)
    right = client.post("/auth/login", json={"email": "signs-in@test.com", "password": "right-password"})
    assert right.status_code == 200
    stamped = _run(user_repo.get_by_id(uid)).last_login_at
    assert stamped is not None and stamped >= before

    row = _row_for(_directory(client).json(), "signs-in@test.com")
    assert _at(row["last_login_at"]) == stamped


# ── Order and pages ──────────────────────────────────────────────────────

@pytest.mark.usefixtures("admin")
def test_each_order_puts_accounts_without_a_value_last(client, services):
    busy = _run(seed_user(services["user_repo"], "directory-busy"))
    quiet = _run(seed_user(services["user_repo"], "directory-quiet"))
    for actor in (busy, busy, quiet):
        _run(services["activity_repo"].record(ActivityEvent(
            actor_id=actor.id, entity_type="issue", entity_id="i",
            action="created", summary="i")))
    _run(services["user_repo"].record_login(quiet.id, NOW))

    by_activity = [user["email"] for user in _directory(client, sort="activity_count").json()["users"]]
    assert by_activity[:2] == [busy.email, quiet.email]

    by_login = _directory(client, sort="last_login").json()["users"]
    assert by_login[0]["email"] == quiet.email
    assert all(user["last_login_at"] is None for user in by_login[1:])


@pytest.mark.usefixtures("admin")
def test_pages_neither_overlap_nor_skip_and_carry_the_total(client, services):
    for i in range(5):
        _run(seed_user(services["user_repo"], f"directory-page-{i}"))

    first = _directory(client, limit=4, offset=0).json()
    second = _directory(client, limit=4, offset=4).json()

    assert first["total"] == second["total"] == 6
    ids = [user["id"] for user in first["users"] + second["users"]]
    assert len(ids) == len(set(ids)) == 6


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 201}, {"offset": -1}, {"sort": "email"}])
@pytest.mark.usefixtures("admin")
def test_out_of_bounds_requests_are_rejected(client, params):
    assert _directory(client, **params).status_code == 422
