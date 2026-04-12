from __future__ import annotations

import os

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from src.domain.user import User
from src.repositories.user_repository import UserRepository

JWT_SECRET = os.environ.get("JWT_SECRET", "dev-secret-do-not-use-in-production")
JWT_ALGORITHM = "HS256"

_bearer_scheme = HTTPBearer()


@inject
async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    user_repo: FromDishka[UserRepository] = None,  # type: ignore[assignment]
) -> User:
    token = credentials.credentials
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    import uuid as _uuid

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
