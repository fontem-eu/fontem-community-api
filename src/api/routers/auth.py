"""Authentication endpoints — Google OAuth + local accounts."""
from __future__ import annotations

import base64
import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import httpx
from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, Depends, HTTPException
from jose import jwt as jose_jwt
from pydantic import BaseModel, EmailStr

from src.api.auth import JWT_ALGORITHM, JWT_SECRET
from src.domain.user import User
from src.repositories.user_repository import UserRepository

router = APIRouter(prefix="/auth", tags=["Auth"])

GOOGLE_CLIENT_ID = os.environ.get(
    "GOOGLE_CLIENT_ID",
    "1075253652266-hbea8sdsn4ihh6as732duohspgvf5eh4.apps.googleusercontent.com",
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
    except (ValueError, UnicodeDecodeError) as exc:
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


@router.post("/google", response_model=TokenResponse)
@inject
async def google_login(
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
        ),
    )


# ── Local account registration + login ────────────────────────

class RegisterRequest(BaseModel):
    """Local account registration."""
    email: str
    password: str
    name: str


class LoginRequest(BaseModel):
    """Local account login."""
    email: str
    password: str


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _check_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


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
        ),
    )


@router.post("/register", response_model=TokenResponse, status_code=201)
@inject
async def register(
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


@router.post("/login", response_model=TokenResponse)
@inject
async def login(
    body: LoginRequest,
    *,
    user_repo: FromDishka[UserRepository],
) -> TokenResponse:
    """Login with email + password."""
    user = await user_repo.get_by_email(body.email)
    if user is None or user.password_hash is None:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not _check_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    # Check ban
    sanction = await user_repo.get_active_sanction(user.id)
    if sanction is not None and sanction.type == "ban":
        raise HTTPException(status_code=401, detail="Account is banned")

    return _issue_jwt(user)
