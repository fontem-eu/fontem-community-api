"""Authentication endpoints — Google OAuth + local accounts.

Session model (2026-06-13, closes review #6):

- **Access JWT**: 15-minute TTL, returned in the JSON response body.
  The SPA keeps it in memory only (NOT localStorage) so an XSS
  regression can't exfil long-lived auth.
- **Refresh token**: 14-day TTL, opaque random 32 bytes, set as a
  ``HttpOnly; SameSite=Lax; Secure`` cookie. The browser stores it
  but JS can't read it — only the server sees the value, and only
  via the cookie header on /auth/refresh and /auth/logout.
- **Family rotation**: every /auth/refresh swaps the family's stored
  hash atomically. Two parallel refreshes race for the row; the
  loser gets 401. Replaying an already-rotated token (the "stolen
  refresh" case) hits a hash that's no longer current and gets 401.
- **Logout**: revokes the family the cookie carries.
- **Sign-out-everywhere**: revokes every active family for the user.

What's intentionally absent: an admin "revoke this user's sessions"
verb. The platform UI exposes the same control to the user
themselves on the account-settings page; we don't want operators
with one-click force-logout capability.
"""
# NOTE: deliberately *no* ``from __future__ import annotations`` here.
# The handlers return ``TokenResponse`` (a Pydantic model defined later
# in the file). With the future import, FastAPI gets a ForwardRef it
# can't resolve when building the response serializer and crashes the
# request with ``PydanticUserError`` (model not fully defined). Sonar's
# python:S8409 wants ``response_model=`` dropped because the return
# annotation already conveys it — but that only works if the
# annotation evaluates to the actual class, not a string. So we keep
# the regular (eager) annotations and accept that S8409 is happy.
import base64
import hashlib
import json
import os
import uuid
from datetime import datetime, timedelta, timezone

from typing import Annotated

import bcrypt
import httpx
from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from jose import jwt as jose_jwt
from pydantic import BaseModel, EmailStr, Field

from src.api.auth import JWT_ALGORITHM, JWT_SECRET, get_current_user
from src.api.openapi_responses import AUTH_RESPONSES
from src.api.rate_limit import limiter
from src.domain.user import User
from src.repositories.user_repository import UserRepository
from src.services.refresh_token_service import (
    InvalidRefreshToken,
    RefreshTokenService,
)
from src.services.email_verification_service import EmailVerificationService
from src.services.password_reset_service import PasswordResetService

# /auth/* surface returns 400 (body validation), 401 (bad credentials),
# 409 (email already registered), and 429 (rate-limited by the
# @limiter.limit decorators on each handler) on top of the AUTH_RESPONSES
# baseline. Documented at the router level so schemathesis's "undocumented
# HTTP status code" check stops flagging them.
router = APIRouter(
    prefix="/auth",
    tags=["Auth"],
    responses={
        **AUTH_RESPONSES,
        400: {
            "description": (
                "Body validation failed at the handler layer "
                "(e.g. password shorter than 8 characters)."
            ),
        },
        409: {
            "description": (
                "Conflict — typically the email address is already "
                "registered to another account."
            ),
        },
    },
)

# Brute-force protection: lock account for 15 min after 5 failed attempts
_MAX_LOGIN_ATTEMPTS = 5

# Banned-account 401 message. Extracted to a constant because three
# code paths raise the same string (Google login, password login,
# refresh-on-banned-mid-session) and Sonar S1192 flags the
# duplication.
_BANNED_DETAIL = "Account is banned"

_LOCKOUT_MINUTES = 15

GOOGLE_CLIENT_ID = os.environ.get(
    "GOOGLE_CLIENT_ID",
    # Fontem Google OAuth client — ID-token verification uses this as
    # the audience. Must match the client_id the frontend initialises
    # GSI with (gmr-web/src/views/LoginView.vue). Rotate in lockstep.
    "1055538305131-87jn8h6gunj55q1akfdkuv6kpg43ld4t.apps.googleusercontent.com",
)

# Access JWT TTL — short by design. The SPA keeps the token in
# memory and silently refreshes via the httpOnly cookie when it
# expires; the user-visible session lasts for the 14-day refresh-
# token window, not this number. Pre-2026-06-13 this was 30 days
# (review finding #6); the long-lived value now lives in the refresh
# family server-side.
_ACCESS_TOKEN_TTL = timedelta(minutes=15)


# ── Cookie helpers ──────────────────────────────────────────────────

# The cookie name is namespaced so a co-resident JS library can't
# accidentally clobber it. ``HttpOnly`` keeps it out of ``document.cookie``;
# ``Secure`` requires HTTPS so a downgrade can't strip it; ``SameSite=Lax``
# blocks the standard cross-site CSRF attack without breaking
# the legitimate top-level navigation flows (clicks from external links).
_REFRESH_COOKIE_NAME = "fontem_refresh"


def _cookie_secure() -> bool:
    """Set Secure on the cookie unless ``FONTEM_COOKIE_INSECURE=1`` —
    only the test conftest sets that, because Starlette's TestClient
    speaks plain http and a Secure cookie wouldn't get echoed back."""
    return os.environ.get("FONTEM_COOKIE_INSECURE") != "1"


def _set_refresh_cookie(response: Response, plaintext: str, ttl_seconds: int) -> None:
    response.set_cookie(
        key=_REFRESH_COOKIE_NAME,
        value=plaintext,
        max_age=ttl_seconds,
        httponly=True,
        secure=_cookie_secure(),
        samesite="lax",
        # The cookie applies to every API path. ``/`` is the root for
        # the SPA-on-the-same-origin setup we ship; refresh + logout
        # both live under /auth so anything tighter than ``/`` would
        # need a separate cookie for the API client to read. Not worth
        # the split.
        path="/",
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        key=_REFRESH_COOKIE_NAME,
        path="/",
        httponly=True,
        secure=_cookie_secure(),
        samesite="lax",
    )


def _hash_request_fingerprint(value: str | None) -> str | None:
    """SHA-256 a request header so we can store a forensic fingerprint
    without keeping the plaintext value (UA strings + IPs are PII)."""
    if not value:
        return None
    return hashlib.sha256(value.encode()).hexdigest()


# ── Request / response shapes ──────────────────────────────────────


class GoogleTokenRequest(BaseModel):
    """Incoming Google ID token from the frontend GSI callback."""

    credential: str


class UserInfo(BaseModel):
    """Public user info returned alongside the session token."""

    id: str
    email: str
    name: str
    avatar_url: str | None = None
    # Coarse authorization hint for the frontend — lets the UI hide
    # moderator-only affordances (e.g. the Admin link in the footer)
    # without requiring a round-trip to /users/me on every navigation.
    # The server remains the source of truth and re-checks on every
    # privileged endpoint.
    trust_level: str = "new_user"
    # Lets the SPA render the "confirm your email" interstitial + gate
    # the compose affordances without a separate round-trip. The
    # server still enforces the gate on every participation action.
    email_verified: bool = True


class TokenResponse(BaseModel):
    """Session JWT issued after successful authentication.

    No refresh token in the body — it rides in an httpOnly cookie so
    JS can't read it.
    """

    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserInfo


class LogoutResponse(BaseModel):
    ok: bool = True
    sessions_revoked: int = 1


# ── Token issuance ─────────────────────────────────────────────────


def _mint_access_jwt(user: User) -> str:
    now = datetime.now(timezone.utc)
    expires = now + _ACCESS_TOKEN_TTL
    return jose_jwt.encode(
        {
            "sub": user.id,
            "email": user.email,
            "name": user.name,
            "iat": int(now.timestamp()),
            "exp": int(expires.timestamp()),
        },
        JWT_SECRET,
        algorithm=JWT_ALGORITHM,
    )


def _to_token_response(user: User, access_jwt: str) -> TokenResponse:
    return TokenResponse(
        access_token=access_jwt,
        expires_in=int(_ACCESS_TOKEN_TTL.total_seconds()),
        user=UserInfo(
            id=user.id,
            email=user.email,
            name=user.name,
            avatar_url=user.avatar_url,
            trust_level=user.trust_level,
            email_verified=user.email_verified_at is not None,
        ),
    )


async def _issue_session(
    user: User,
    request: Request,
    response: Response,
    refresh_service: RefreshTokenService,
) -> TokenResponse:
    """Mint access JWT + open a refresh family + set the cookie.

    Single seam used by every successful login path (Google, local,
    register) so the cookie semantics stay byte-for-byte identical.
    """
    issued = await refresh_service.issue_for_login(
        user_id=user.id,
        user_agent_hash=_hash_request_fingerprint(
            request.headers.get("user-agent"),
        ),
        ip_hash=_hash_request_fingerprint(
            request.client.host if request.client else None,
        ),
    )
    ttl_seconds = int(
        (issued.family.expires_at - datetime.now(timezone.utc)).total_seconds(),
    )
    _set_refresh_cookie(response, issued.plaintext, ttl_seconds=ttl_seconds)
    return _to_token_response(user, _mint_access_jwt(user))


# ── Google OAuth ───────────────────────────────────────────────────


async def _verify_google_token(credential: str) -> dict:
    """Verify a Google ID token using Google's public RSA keys."""
    async with httpx.AsyncClient() as client:
        resp = await client.get("https://www.googleapis.com/oauth2/v3/certs")
        resp.raise_for_status()
        keys = resp.json()

    parts = credential.split(".")
    if len(parts) != 3:
        raise HTTPException(status_code=401, detail="Invalid Google token: not a JWT")
    header_segment = parts[0]
    padding = 4 - len(header_segment) % 4
    if padding != 4:
        header_segment += "=" * padding
    try:
        header = json.loads(base64.urlsafe_b64decode(header_segment))
    except ValueError as exc:
        raise HTTPException(
            status_code=401, detail="Invalid Google token: malformed header",
        ) from exc
    kid = header.get("kid")

    matching_key = next(
        (k for k in keys.get("keys", []) if k["kid"] == kid),
        None,
    )
    if matching_key is None:
        raise HTTPException(status_code=401, detail="Invalid Google token: unknown key")

    try:
        payload = jose_jwt.decode(
            credential,
            matching_key,
            algorithms=["RS256"],
            audience=GOOGLE_CLIENT_ID,
            issuer=["accounts.google.com", "https://accounts.google.com"],
        )
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid Google token") from exc

    if not payload.get("email_verified", False):
        raise HTTPException(status_code=401, detail="Email not verified by Google")

    return payload


# slowapi's @limiter.limit decorator extracts the client IP off the
# first positional Request argument; the handler body uses it for the
# session fingerprint too. Same on /register and /login below.
@router.post(
    "/google",
    responses={
        401: {
            "description": (
                "Google token verification failed (malformed JWT, "
                "unknown signing key, decode error, email not verified, "
                "or the account is banned)."
            ),
        },
    },
)
@limiter.limit("10/minute")
@inject
# pylint: disable-next=too-many-arguments,too-many-positional-arguments
async def google_login(
    request: Request,
    response: Response,
    body: GoogleTokenRequest,
    *,
    user_repo: FromDishka[UserRepository],
    refresh_service: FromDishka[RefreshTokenService],
) -> TokenResponse:
    """Exchange a Google ID token for a Fontem session JWT + refresh cookie."""
    payload = await _verify_google_token(body.credential)

    email = payload["email"]
    name = payload.get("name", email.split("@")[0])
    picture = payload.get("picture")
    google_sub = payload["sub"]

    # Upsert user — derive a deterministic UUID from the Google sub
    # so the ID is stable across logins and compatible with the UUID column
    user_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"google:{google_sub}"))
    existing = await user_repo.get_by_id(user_id)
    if existing is None:
        existing = await user_repo.get_by_email(email)

    if existing is not None:
        existing.name = name
        existing.avatar_url = picture
        user = await user_repo.upsert(existing)
    else:
        user = User(id=user_id, email=email, name=name, avatar_url=picture)
        user = await user_repo.upsert(user)

    sanction = await user_repo.get_active_sanction(user.id)
    if sanction is not None and sanction.type == "ban":
        raise HTTPException(status_code=401, detail=_BANNED_DETAIL)

    return await _issue_session(user, request, response, refresh_service)


# ── Local account registration + login ─────────────────────────────


class RegisterRequest(BaseModel):
    """Local account registration."""
    email: EmailStr
    # 8-char minimum mirrors the runtime check below — declaring it on
    # the Pydantic field gets the constraint into the OpenAPI schema so
    # fuzz tooling stops generating 4-char passwords and flagging the
    # legitimate 400 as "API rejected schema-compliant request".
    password: str = Field(min_length=8, max_length=128)
    name: str = Field(min_length=1, max_length=200)


class LoginRequest(BaseModel):
    """Local account login."""
    email: str
    password: str


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _check_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


# Constant-time bcrypt dummy. Computed once at import (the cost of
# bcrypt.gensalt() is what's expensive, not checkpw) so the missing-
# user login path takes the same wall-clock as a real bcrypt verify.
# Closes the timing oracle documented in the 2026-06-11 security
# review finding #2: pre-fix a real-but-wrong-password login took
# ~220 ms while a fake-email login returned in ~13 ms, leaking
# account existence at ~3600 emails/day per IP under the 5/min rate
# limit. With this dummy in place both paths run a full bcrypt round.
#
# Source bytes are `os.urandom` rather than a literal: there's no
# real credential here (this hash is never compared against user
# input — it's only the second arg to a bcrypt.checkpw whose result
# is discarded), but a per-process random source means the hash
# can't be precomputed offline either, and Sonar S6437 doesn't flag
# it as a hardcoded password.
_DUMMY_PASSWORD_HASH = bcrypt.hashpw(
    os.urandom(16), bcrypt.gensalt(),
).decode()


@router.post(
    "/register",
    status_code=201,
    responses={
        400: {"description": "Password shorter than 8 characters."},
        409: {"description": "Email already registered to another account."},
    },
)
@limiter.limit("3/minute")
@inject
# pylint: disable-next=too-many-arguments,too-many-positional-arguments
async def register(
    request: Request,
    response: Response,
    body: RegisterRequest,
    *,
    user_repo: FromDishka[UserRepository],
    refresh_service: FromDishka[RefreshTokenService],
    verify_service: FromDishka[EmailVerificationService],
) -> TokenResponse:
    """Register a new local account.

    The account is created **unverified** (email_verified_at is NULL).
    A session is still issued so the SPA can render the "check your
    email" interstitial and offer resend/logout — but every
    participation action 403s ("email not verified") until the user
    clicks the link. See the authz policy's _VERIFIED_REQUIRED set.
    """
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    existing = await user_repo.get_by_email(body.email)
    if existing is not None:
        raise HTTPException(status_code=409, detail="Email already registered")

    user_id = str(uuid.uuid4())
    user = User(
        id=user_id,
        email=body.email,
        name=body.name,
        password_hash=_hash_password(body.password),
    )
    user = await user_repo.upsert(user)
    # Fire the verification mail. issue() swallows mail-provider errors
    # so a flaky Brevo never 500s a registration — the token is
    # persisted and the user can hit "resend".
    await verify_service.issue(user)
    return await _issue_session(user, request, response, refresh_service)


@router.post(
    "/login",
    responses={
        401: {
            "description": (
                "Invalid email or password, or the account is banned."
            ),
        },
        429: {
            "description": (
                "Account temporarily locked after too many failed login "
                "attempts. Different from the router-level ingress 429 — "
                "this one is account-level brute-force protection."
            ),
        },
    },
)
@limiter.limit("5/minute")
@inject
# pylint: disable-next=too-many-arguments,too-many-positional-arguments
async def login(
    request: Request,
    response: Response,
    body: LoginRequest,
    *,
    user_repo: FromDishka[UserRepository],
    refresh_service: FromDishka[RefreshTokenService],
) -> TokenResponse:
    """Login with email + password."""
    user = await user_repo.get_by_email(body.email)

    # Always run bcrypt — even when no user matches — so the response
    # time can't distinguish "email exists" from "email doesn't". See
    # the _DUMMY_PASSWORD_HASH comment for the timing-oracle context.
    candidate_hash = (
        user.password_hash
        if user is not None and user.password_hash is not None
        else _DUMMY_PASSWORD_HASH
    )
    password_ok = _check_password(body.password, candidate_hash)

    if user is None or user.password_hash is None:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    # Lockout check (account-level brute-force protection)
    if user.locked_until is not None and user.locked_until > datetime.now(timezone.utc):
        raise HTTPException(
            status_code=429,
            detail=f"Account temporarily locked. Try again in {_LOCKOUT_MINUTES} minutes.",
        )

    if not password_ok:
        await user_repo.register_failed_login(
            body.email, _MAX_LOGIN_ATTEMPTS, _LOCKOUT_MINUTES,
        )
        raise HTTPException(status_code=401, detail="Invalid email or password")

    sanction = await user_repo.get_active_sanction(user.id)
    if sanction is not None and sanction.type == "ban":
        raise HTTPException(status_code=401, detail=_BANNED_DETAIL)

    await user_repo.clear_failed_logins(user.id)

    return await _issue_session(user, request, response, refresh_service)


# ── Refresh + logout + sign-out-everywhere ─────────────────────────


@router.post(
    "/refresh",
    responses={
        401: {
            "description": (
                "Refresh cookie missing, expired, or invalidated. The "
                "SPA's silent-refresh path treats this as 'log out and "
                "redirect to /login'."
            ),
        },
    },
)
@limiter.limit("30/minute")
@inject
# pylint: disable-next=too-many-arguments,too-many-positional-arguments
async def refresh(
    request: Request,
    response: Response,
    *,
    user_repo: FromDishka[UserRepository],
    refresh_service: FromDishka[RefreshTokenService],
) -> TokenResponse:
    """Rotate the session — new access JWT, new refresh cookie.

    The cookie carries the current plaintext refresh token. We rotate
    atomically; success returns a fresh JWT + sets a new cookie. Any
    failure (unknown token, expired family, lost race) clears the
    cookie and returns 401 so the SPA redirects to /login.
    """
    offered = request.cookies.get(_REFRESH_COOKIE_NAME)
    if not offered:
        raise HTTPException(status_code=401, detail="No refresh cookie")

    try:
        issued = await refresh_service.rotate(offered)
    except InvalidRefreshToken as e:
        _clear_refresh_cookie(response)
        raise HTTPException(status_code=401, detail=str(e)) from e

    user = await user_repo.get_by_id(issued.family.user_id)
    if user is None:
        # The user was deleted while the session was live. Treat as
        # logout — clear the cookie, fail the request.
        _clear_refresh_cookie(response)
        raise HTTPException(status_code=401, detail="User no longer exists")

    # Refuse the refresh if the user got banned mid-session. They
    # could otherwise stay logged in until their access JWT expires
    # (15 min), which is the right blast-radius but still worth
    # killing at refresh time.
    sanction = await user_repo.get_active_sanction(user.id)
    if sanction is not None and sanction.type == "ban":
        await refresh_service.revoke(issued.plaintext)
        _clear_refresh_cookie(response)
        raise HTTPException(status_code=401, detail=_BANNED_DETAIL)

    ttl_seconds = int(
        (issued.family.expires_at - datetime.now(timezone.utc)).total_seconds(),
    )
    _set_refresh_cookie(response, issued.plaintext, ttl_seconds=ttl_seconds)
    return _to_token_response(user, _mint_access_jwt(user))


@router.post("/logout")
@inject
async def logout(
    request: Request,
    response: Response,
    *,
    refresh_service: FromDishka[RefreshTokenService],
) -> LogoutResponse:
    """Revoke the current session and clear the cookie.

    Idempotent — calling /auth/logout twice is fine. The endpoint is
    intentionally not auth-gated: the cookie itself is the credential,
    and we want a logout to succeed even if the access JWT has
    already expired (otherwise the user couldn't ever sign out from
    a stale tab without first triggering a refresh).
    """
    offered = request.cookies.get(_REFRESH_COOKIE_NAME)
    if offered:
        await refresh_service.revoke(offered)
    _clear_refresh_cookie(response)
    return LogoutResponse(ok=True, sessions_revoked=1)


@router.post("/sign_out_everywhere")
@inject
async def sign_out_everywhere(
    response: Response,
    *,
    refresh_service: FromDishka[RefreshTokenService],
    user: Annotated[User, Depends(get_current_user)],
) -> LogoutResponse:
    """Revoke every active session for the calling user.

    Auth-gated by the access JWT (so a stolen *refresh token* alone
    can't trigger this — the attacker would also need a valid access
    token, which expires every 15 min and gives the legitimate user
    a window to notice). User-facing only — there is no admin verb.
    """
    revoked = await refresh_service.revoke_all_for_user(user.id)
    _clear_refresh_cookie(response)
    return LogoutResponse(ok=True, sessions_revoked=revoked)


# ── Email verification + password reset ────────────────────────────


class VerifyEmailRequest(BaseModel):
    """Token from the verification link (SPA reads ?token= and POSTs it)."""
    token: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=8, max_length=128)


class SimpleOk(BaseModel):
    ok: bool = True


@router.post(
    "/verify-email",
    responses={
        400: {"description": "Token missing, expired, already used, or invalid."},
    },
)
@limiter.limit("10/minute")
@inject
async def verify_email(
    request: Request,  # pylint: disable=unused-argument
    body: VerifyEmailRequest,
    *,
    verify_service: FromDishka[EmailVerificationService],
) -> SimpleOk:
    """Redeem an email-verification link.

    Verification takes effect immediately — the AuthorizationService
    rebuilds the Principal from the DB every request, so the caller's
    next participation action succeeds without re-login.
    """
    user = await verify_service.consume(body.token)
    if user is None:
        raise HTTPException(
            status_code=400,
            detail="Verification link is invalid, expired, or already used.",
        )
    return SimpleOk(ok=True)


@router.post("/resend-verification")
@limiter.limit("3/minute")
@inject
# pylint: disable-next=too-many-arguments,too-many-positional-arguments
async def resend_verification(
    request: Request,  # pylint: disable=unused-argument
    *,
    user_repo: FromDishka[UserRepository],
    verify_service: FromDishka[EmailVerificationService],
    user: Annotated[User, Depends(get_current_user)],
) -> SimpleOk:
    """Re-send the verification link for the signed-in account.

    Auth-gated (you have to be logged in as the account) + rate
    limited. No-ops silently if the account is somehow already
    verified, so the response shape can't be used to probe state.
    """
    fresh = await user_repo.get_by_id(user.id)
    if fresh is not None and fresh.email_verified_at is None:
        await verify_service.issue(fresh)
    return SimpleOk(ok=True)


@router.post("/forgot")
@limiter.limit("3/minute")
@inject
async def forgot_password(
    request: Request,  # pylint: disable=unused-argument
    body: ForgotPasswordRequest,
    *,
    reset_service: FromDishka[PasswordResetService],
) -> SimpleOk:
    """Request a password-reset link.

    ALWAYS returns 200 ok regardless of whether the email matches an
    account — no account enumeration. The reset service silently
    no-ops for unknown / OAuth-only emails.
    """
    await reset_service.request(body.email)
    return SimpleOk(ok=True)


@router.post(
    "/reset",
    responses={
        400: {"description": "Reset token missing, expired, already used, or invalid."},
    },
)
@limiter.limit("5/minute")
@inject
async def reset_password(
    request: Request,  # pylint: disable=unused-argument
    body: ResetPasswordRequest,
    *,
    reset_service: FromDishka[PasswordResetService],
) -> SimpleOk:
    """Redeem a reset token + set a new password.

    On success every refresh-token family for the account is revoked
    — a reset is the account-recovery path, so any session an attacker
    holds dies here. The user re-logs in with the new password.
    """
    ok = await reset_service.reset(body.token, body.new_password)
    if not ok:
        raise HTTPException(
            status_code=400,
            detail="Reset link is invalid, expired, or already used.",
        )
    return SimpleOk(ok=True)
