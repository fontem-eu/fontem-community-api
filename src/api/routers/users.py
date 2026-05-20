from __future__ import annotations

from dataclasses import asdict
from typing import Annotated

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.auth import get_current_user
from src.api.openapi_responses import RESOURCE_RESPONSES, UuidPath
from src.assistant.repository import AssistRepository
from src.domain.user import User
from src.repositories.user_repository import UserRepository
from src.services.exceptions import NotFound

router = APIRouter(prefix="/users", tags=["users"], responses=RESOURCE_RESPONSES)


@router.get("/me")
async def get_me(
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    return asdict(user)


@router.delete("/me", status_code=204)
@inject
async def delete_me(
    *,
    session: FromDishka[AsyncSession],
    assist_repo: FromDishka[AssistRepository],
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    """Delete the current user's account and all associated data (GDPR Art. 17).

    Deletes data in FK-safe order across all 14 user-referencing tables.
    Conversations, reports, sections, comments, issues, votes, flags,
    sanctions, moderation log entries, group memberships, and the user
    record itself are all removed.
    """
    uid = user.id

    # Assist conversations (and their messages via FK cascade)
    await assist_repo.delete_user_conversations(uid)

    # Tables referencing users.id, ordered to satisfy FK constraints.
    # Most have no cascade so we delete them explicitly. Reports cascade
    # to sections / section_versions / report_access / conversations via
    # ON DELETE CASCADE on the report_id FK in each child table.
    cleanup = [
        "DELETE FROM conversations WHERE user_id = :uid",
        "DELETE FROM flags WHERE flagged_by = :uid",
        "DELETE FROM sanctions WHERE user_id = :uid OR applied_by = :uid",
        "DELETE FROM moderation_log WHERE actor_id = :uid",
        "DELETE FROM issue_votes WHERE user_id = :uid",
        "DELETE FROM comments WHERE author_id = :uid",
        "DELETE FROM issues WHERE created_by = :uid",
        "UPDATE sections SET lock_holder = NULL WHERE lock_holder = :uid",
        # Versions on other people's reports — own-report versions
        # cascade-delete with the report itself.
        (
            "DELETE FROM section_versions "
            "WHERE saved_by = :uid AND section_id NOT IN ("
            "  SELECT id FROM sections WHERE report_id IN ("
            "    SELECT id FROM reports WHERE created_by = :uid"
            "  )"
            ")"
        ),
        "DELETE FROM report_access WHERE user_id = :uid",
        "DELETE FROM reports WHERE created_by = :uid",
        "DELETE FROM group_members WHERE user_id = :uid",
        "DELETE FROM user_roles WHERE user_id = :uid",
        "DELETE FROM users WHERE id = :uid",
    ]
    for stmt in cleanup:
        await session.execute(text(stmt), {"uid": uid})
    await session.commit()


@router.get("/{user_id}")
@inject
async def get_user(
    user_id: UuidPath,
    *,
    repo: FromDishka[UserRepository],
    _user: Annotated[User, Depends(get_current_user)],
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
