"""Every mutating endpoint leaves a trace.

The activity log was opt-in and decayed to exactly that: 29 rows in
production, all about stories, from 6 services out of 27. Studio projects,
plots, flowers, follows and tags changed nothing anyone could see
afterwards.

Coverage now comes from the middleware rather than from remembering, and
this file is what keeps it that way — including the enforcement test at the
bottom, which fails when a new mutating route appears with no coverage.
"""
import jwt
import pytest

from src.api.audit_middleware import MUTATING, SKIP_PREFIXES, should_audit, _actor_from
from src.api.auth import JWT_ALGORITHM, JWT_SECRET


class _Req:
    def __init__(self, headers=None):
        self.headers = headers or {}


def _token(sub):
    return jwt.encode({"sub": sub}, JWT_SECRET, algorithm=JWT_ALGORITHM)


# ── which requests are audited ─────────────────────────────────

@pytest.mark.parametrize("method", sorted(MUTATING))
def test_every_mutating_method_is_audited(method):
    assert should_audit(method, "/capi/studio/projects")


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
def test_reads_are_not(method):
    # A log that records every GET buries what was DONE in noise.
    assert not should_audit(method, "/capi/studio/projects")


def test_method_matching_is_case_insensitive():
    assert should_audit("post", "/capi/anything")


@pytest.mark.parametrize("path", SKIP_PREFIXES)
def test_token_churn_is_not_activity(path):
    # /auth/refresh fires on every cold page load for every visitor.
    assert not should_audit("POST", f"/capi{path}")


def test_a_real_endpoint_that_merely_contains_a_skipped_word_is_still_audited():
    assert should_audit("POST", "/capi/studio/projects/logout-notes")


# ── who the entry is attributed to ─────────────────────────────

def test_the_actor_comes_from_the_bearer_token():
    uid = "3f6c0a2e-1b7d-4f2a-9c3e-0a1b2c3d4e5f"
    assert _actor_from(_Req({"authorization": f"Bearer {_token(uid)}"})) == uid


def test_an_anonymous_request_has_no_actor():
    assert _actor_from(_Req()) == ""


def test_a_forged_token_is_treated_as_anonymous_not_trusted():
    # The route's own auth rejects it; the middleware must not attribute
    # anything to a subject it could not verify.
    bad = jwt.encode({"sub": "someone-else"}, "not-the-secret", algorithm="HS256")
    assert _actor_from(_Req({"authorization": f"Bearer {bad}"})) == ""


def test_a_non_uuid_subject_is_normalised_the_way_auth_does_it():
    # Otherwise the middleware and the routes disagree about who acted, and
    # the entries point at a user id that exists nowhere.
    import uuid as _uuid  # pylint: disable=import-outside-toplevel
    got = _actor_from(_Req({"authorization": f"Bearer {_token('google-123')}"}))
    assert got == str(_uuid.uuid5(_uuid.NAMESPACE_URL, "google-123"))


def test_a_token_without_a_subject_is_anonymous():
    tok = jwt.encode({"foo": "bar"}, JWT_SECRET, algorithm=JWT_ALGORITHM)
    assert _actor_from(_Req({"authorization": f"Bearer {tok}"})) == ""


def test_a_malformed_authorization_header_does_not_raise():
    assert _actor_from(_Req({"authorization": "Basic abc"})) == ""
    assert _actor_from(_Req({"authorization": "Bearer"})) == ""


# ── the enforcement ────────────────────────────────────────────

def test_no_mutating_route_escapes_the_audit_middleware():
    """The test that stops coverage decaying back to six services.

    It does not check that every route records something *specific* — that
    is enrichment, and a generic entry is an acceptable answer. It checks
    that every mutating route is one the middleware will see, so a new
    endpoint cannot be silent by default.
    """
    from src.api.app import app  # pylint: disable=import-outside-toplevel

    missed = []
    for route in app.router.routes:
        path = getattr(route, "path", "")
        for method in getattr(route, "methods", set()) or set():
            if method.upper() not in MUTATING:
                continue
            if not path.startswith("/"):
                continue
            if not should_audit(method, path):
                missed.append(f"{method} {path}")

    # Only the auth endpoints may opt out, and they are named constants.
    for entry in missed:
        assert any(p in entry for p in SKIP_PREFIXES), (
            f"{entry} mutates and is not audited; add it to SKIP_PREFIXES "
            f"with a reason, or leave it covered"
        )
