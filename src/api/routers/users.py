from __future__ import annotations

from dishka.integrations.fastapi import FromDishka, inject

from dataclasses import asdict

from fastapi import APIRouter, Depends

from src.api.auth import get_current_user
from src.domain.user import User
from src.repositories.user_repository import UserRepository
from src.services.exceptions import NotFound

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me")
async def get_me(
    user: User = Depends(get_current_user),
) -> dict:
    return asdict(user)


@router.get("/{user_id}")
@inject
async def get_user(
    user_id: str,
    *,
    repo: FromDishka[UserRepository],
    user: User = Depends(get_current_user),
) -> dict:
    target = await repo.get_by_id(user_id)
    if target is None:
        raise NotFound(f"User {user_id} not found")
    return {
        "id": target.id,
        "name": target.name,
        "avatar_url": target.avatar_url,
        "trust_level": target.trust_level,
    }
