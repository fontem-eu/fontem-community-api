from __future__ import annotations

import os
import uuid as _uuid

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from src.domain.user import User
from src.repositories.user_repository import UserRepository

JWT_SECRET = os.environ.get("JWT_SECRET", "dev-secret-do-not-use-in-production")
JWT_ALGORITHM = "HS256"

_bearer_scheme = HTTPBearer()


async def _resolve_user(
    credentials: HTTPAuthorizationCredentials,
    user_repo: UserRepository,
) -> User:
    """Core token → User logic; shared by the strict and optional deps."""
    token = credentials.credentials
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired token") from exc

    user_id: str | None = payload.get("sub")
    if user_id is None:
        raise HTTPException(status_code=401, detail="Token missing 'sub' claim")

    # Ensure user_id is a valid UUID. Non-UUID subs (legacy Google tokens,
    # test tokens, etc.) get a deterministic UUID5 derived from the sub value.
    try:
        _uuid.UUID(user_id)
    except ValueError:
        user_id = str(_uuid.uuid5(_uuid.NAMESPACE_URL, user_id))

    # Auto-create user on first request
    user = await user_repo.get_by_id(user_id)
    if user is None:
        email = payload.get("email", f"{user_id}@unknown")
        name = payload.get("name", user_id)
        user = User(id=user_id, email=email, name=name)
        user = await user_repo.upsert(user)

    # Check ban
    sanction = await user_repo.get_active_sanction(user_id)
    if sanction is not None and sanction.type == "ban":
        raise HTTPException(status_code=401, detail="Account is banned")

    return user


@inject
async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    user_repo: FromDishka[UserRepository] = None,  # type: ignore[assignment]
) -> User:
    return await _resolve_user(credentials, user_repo)


@inject
async def get_optional_user(
    request: Request,
    user_repo: FromDishka[UserRepository] = None,  # type: ignore[assignment]
) -> User | None:
    """Auth dep that returns None for anonymous callers instead of 401-ing.

    Use on endpoints that must serve unauthenticated clients for at least
    some code paths (e.g. public feed browsing). A malformed token is
    treated as anonymous rather than a hard error — routes that truly
    require auth should keep using ``get_current_user``.

    Parses ``Authorization`` straight off the request rather than going
    through ``HTTPBearer``: doing so deliberately keeps the security
    primitive out of FastAPI's dependency-tree introspection, which is
    what populates the route's OpenAPI ``security`` block. Routes that
    use this dep are correctly emitted as "no auth required" in the
    spec — schemathesis and any other OpenAPI-driven client stop
    flagging the 2xx anonymous responses as "API accepts requests
    without authentication". The runtime semantics (anonymous → None,
    signed-in → User) are unchanged.
    """
    header = request.headers.get("authorization")
    if not header:
        return None
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    credentials = HTTPAuthorizationCredentials(scheme=scheme, credentials=token)
    try:
        return await _resolve_user(credentials, user_repo)
    except HTTPException:
        return None
