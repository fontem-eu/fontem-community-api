"""Authentication endpoints — Google OAuth + local accounts."""
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
import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import httpx
from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, HTTPException, Request
from jose import jwt as jose_jwt
from pydantic import BaseModel, EmailStr, Field

from src.api.auth import JWT_ALGORITHM, JWT_SECRET
from src.api.openapi_responses import AUTH_RESPONSES
from src.api.rate_limit import limiter
from src.domain.user import User
from src.repositories.user_repository import UserRepository

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
_LOCKOUT_MINUTES = 15

GOOGLE_CLIENT_ID = os.environ.get(
    "GOOGLE_CLIENT_ID",
    # Fontem Google OAuth client — ID-token verification uses this as
    # the audience. Must match the client_id the frontend initialises
    # GSI with (gmr-web/src/views/LoginView.vue). Rotate in lockstep.
    "1055538305131-87jn8h6gunj55q1akfdkuv6kpg43ld4t.apps.googleusercontent.com",
)

_TOKEN_EXPIRE_DAYS = 30


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


class TokenResponse(BaseModel):
    """Session JWT issued after successful Google authentication."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserInfo


async def _verify_google_token(credential: str) -> dict:
    """Verify a Google ID token using Google's public RSA keys."""
    async with httpx.AsyncClient() as client:
        resp = await client.get("https://www.googleapis.com/oauth2/v3/certs")
        resp.raise_for_status()
        keys = resp.json()

    # Decode JWT header to find the signing key id
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
# first positional Request argument; the handler body doesn't use it,
# but the parameter has to be named ``request`` and typed Request so
# slowapi can find it. Same on /register and /login below.
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
async def google_login(
    request: Request,  # pylint: disable=unused-argument
    body: GoogleTokenRequest,
    *,
    user_repo: FromDishka[UserRepository],
) -> TokenResponse:
    """Exchange a Google ID token for a GMR session JWT."""
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

    # Check ban
    sanction = await user_repo.get_active_sanction(user.id)
    if sanction is not None and sanction.type == "ban":
        raise HTTPException(status_code=401, detail="Account is banned")

    # Issue JWT
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=_TOKEN_EXPIRE_DAYS)
    token = jose_jwt.encode(
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

    return TokenResponse(
        access_token=token,
        expires_in=_TOKEN_EXPIRE_DAYS * 86400,
        user=UserInfo(
            id=user.id,
            email=user.email,
            name=user.name,
            avatar_url=user.avatar_url,
            trust_level=user.trust_level,
        ),
    )


# ── Local account registration + login ────────────────────────

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


def _issue_jwt(user: User) -> TokenResponse:
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=_TOKEN_EXPIRE_DAYS)
    token = jose_jwt.encode(
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
    return TokenResponse(
        access_token=token,
        expires_in=_TOKEN_EXPIRE_DAYS * 86400,
        user=UserInfo(
            id=user.id, email=user.email,
            name=user.name, avatar_url=user.avatar_url,
            trust_level=user.trust_level,
        ),
    )


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
async def register(
    request: Request,  # pylint: disable=unused-argument
    body: RegisterRequest,
    *,
    user_repo: FromDishka[UserRepository],
) -> TokenResponse:
    """Register a new local account."""
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
    return _issue_jwt(user)


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
async def login(
    request: Request,  # pylint: disable=unused-argument
    body: LoginRequest,
    *,
    user_repo: FromDishka[UserRepository],
) -> TokenResponse:
    """Login with email + password."""
    user = await user_repo.get_by_email(body.email)

    # Always run bcrypt — even when no user matches — so the response
    # time can't distinguish "email exists" from "email doesn't". See
    # the _DUMMY_PASSWORD_HASH comment for the timing-oracle context.
    # The verify result is unused on the no-user path; the branch
    # below still 401s. The lockout check is deliberately *after* the
    # bcrypt round so a locked account doesn't return earlier than a
    # ban-checked-and-rejected one.
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

    # Check ban
    sanction = await user_repo.get_active_sanction(user.id)
    if sanction is not None and sanction.type == "ban":
        raise HTTPException(status_code=401, detail="Account is banned")

    # Successful login — clear any prior failed attempts
    await user_repo.clear_failed_logins(user.id)

    return _issue_jwt(user)
