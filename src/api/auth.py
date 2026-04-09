from __future__ import annotations

import os

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from src.api.dependencies import get_db_session, get_user_repo
from src.domain.user import User
from src.repositories.user_repository import UserRepository

JWT_SECRET = os.environ.get("JWT_SECRET", "dev-secret-do-not-use-in-production")
JWT_ALGORITHM = "HS256"

_bearer_scheme = HTTPBearer()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    _session=Depends(get_db_session),  # ensures session lifecycle (commit/rollback)
    user_repo: UserRepository = Depends(get_user_repo),
) -> User:
    token = credentials.credentials
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    user_id: str | None = payload.get("sub")
    if user_id is None:
        raise HTTPException(status_code=401, detail="Token missing 'sub' claim")

    # Legacy tokens may have "google:XXXXX" as sub instead of a UUID5.
    # Convert to the deterministic UUID5 used by the current Google login flow.
    if user_id.startswith("google:"):
        import uuid
        google_sub = user_id[len("google:"):]
        user_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"google:{google_sub}"))

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
