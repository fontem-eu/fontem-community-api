from __future__ import annotations

from typing import Annotated

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.auth import get_current_user, get_optional_user
from src.api.openapi_responses import RESOURCE_RESPONSES, UuidPath
from src.assistant.repository import AssistRepository
from src.domain.user import User
from src.repositories.user_repository import UserRepository
from src.services.profile_service import ProfileService
from src.infra.minio_client import MinioStorage
from src.services.file_security import AVATAR_MAX_DIM, make_clamd_client, scan_and_sanitise
from src.services.upload_urls import presign_uploads
from src.services.authz import Action, AuthorizationService
from src.services.authz.policy import ResourceRef
from src.services.exceptions import NotFound

router = APIRouter(prefix="/users", tags=["users"], responses=RESOURCE_RESPONSES)

_CLAMD_SENTINEL = object()
_clamd_client = _CLAMD_SENTINEL  # pylint: disable=invalid-name


def _get_clamd():
    """Lazy singleton clamd client; None in dev/tests (no AV env)."""
    global _clamd_client  # pylint: disable=global-statement
    if _clamd_client is _CLAMD_SENTINEL:
        _clamd_client = make_clamd_client()
    return _clamd_client


def _safe_self_view(user: User) -> dict:
    """Public fields safe to return to the user themselves on /me.

    Explicitly excludes ``password_hash`` (bcrypt secret),
    ``failed_login_attempts`` and ``locked_until`` (account-state PII
    that would help an attacker confirm a hit on an ongoing
    credential-stuffing run). 2026-06-10 Schemathesis caught
    ``asdict(user)`` leaking all three in the /me response — this
    helper is the fix and the OneTrue place that decides what shape
    the self-view returns.
    """
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "avatar_url": user.avatar_url,
        "trust_level": user.trust_level,
        "email_verified": user.email_verified_at is not None,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


def _user_ref(user_id: str) -> ResourceRef:
    """Synthetic ResourceRef for a user. The user *is* their own owner
    for self-only checks, which lets the policy match on ``r.id == p.user_id``."""
    return ResourceRef(kind="user", id=user_id, owner_id=user_id)


@router.get("/me")
@inject
async def get_me(
    *,
    authz: FromDishka[AuthorizationService],
    storage: FromDishka[MinioStorage],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    principal = await authz.principal(user.id)
    await authz.require(principal, Action.USERS_READ_SELF, _user_ref(user.id))
    return presign_uploads(_safe_self_view(user), storage.presigned_get_url)


# pylint: disable-next=too-many-arguments,too-many-positional-arguments
@router.delete("/me", status_code=204)
@inject
async def delete_me(
    *,
    session: FromDishka[AsyncSession],
    assist_repo: FromDishka[AssistRepository],
    authz: FromDishka[AuthorizationService],
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    """Delete the current user's account and all associated data (GDPR Art. 17).

    Deletes data in FK-safe order across all 14 user-referencing tables.
    Conversations, reports, sections, comments, issues, votes, flags,
    sanctions, moderation log entries, group memberships, and the user
    record itself are all removed.
    """
    uid = user.id
    principal = await authz.principal(uid)
    await authz.require(principal, Action.USERS_DELETE_SELF, _user_ref(uid))

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
        "DELETE FROM activity_log WHERE actor_id = :uid",
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
    authz: FromDishka[AuthorizationService],
    storage: FromDishka[MinioStorage],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    principal = await authz.principal(user.id)
    await authz.require(principal, Action.USERS_READ_PUBLIC, _user_ref(user_id))
    target = await repo.get_by_id(user_id)
    if target is None:
        raise NotFound(f"User {user_id} not found")
    return presign_uploads({
        "id": target.id,
        "name": target.name,
        "avatar_url": target.avatar_url,
        "trust_level": target.trust_level,
    }, storage.presigned_get_url)


class ProfileLinkIn(BaseModel):
    name: str = Field(default="", max_length=60)
    url: str = Field(default="", max_length=500)


class ProfileUpdate(BaseModel):
    summary: str = Field(default="", max_length=2000)
    links: list[ProfileLinkIn] = Field(default_factory=list, max_length=20)
    avatar_x: float | None = Field(default=None, ge=0, le=100)
    avatar_y: float | None = Field(default=None, ge=0, le=100)
    name: str | None = Field(default=None, max_length=100)
    show_email: bool | None = None
    use_custom_email: bool | None = None
    custom_email: str | None = Field(default=None, max_length=254)


# Public author profiles are readable anonymously — same transparency stance
# as public stories. `security: []` documents the anonymous path; the handler
# only ever exposes the author's PUBLIC articles (visibility gated by whether
# the caller is signed in).
@router.get("/{user_id}/profile", openapi_extra={"security": []})
@inject
async def get_user_profile(
    user_id: UuidPath,
    *,
    svc: FromDishka[ProfileService],
    storage: FromDishka[MinioStorage],
    user: Annotated[User | None, Depends(get_optional_user)],
) -> dict:
    result = await svc.get_profile(
        user_id, viewer_authed=user is not None,
        caller_id=user.id if user is not None else None,
    )
    return presign_uploads(result, storage.presigned_get_url)


@router.put("/me/profile")
@inject
async def update_my_profile(
    body: ProfileUpdate,
    *,
    svc: FromDishka[ProfileService],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    return await svc.update_own_profile(
        user.id,
        body.summary,
        [{"name": l.name, "url": l.url} for l in body.links],
        avatar_x=body.avatar_x,
        avatar_y=body.avatar_y,
        name=body.name,
        show_email=body.show_email,
        use_custom_email=body.use_custom_email,
        custom_email=body.custom_email,
    )


@router.post("/me/avatar")
@inject
async def upload_my_avatar(
    *,
    file: Annotated[UploadFile, File(...)],
    repo: FromDishka[UserRepository],
    storage: FromDishka[MinioStorage],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Upload the signed-in user's avatar.

    Goes through the same file_security pipeline as story images (magic-byte
    sniff -> raster re-encode / SVG sanitise -> clamd). We store the stable
    ``/uploads/<key>`` reference on the user and return a presigned URL for
    immediate display; subsequent reads presign afresh (TTLs never persist).
    """
    raw = await file.read()
    cleaned = scan_and_sanitise(raw, clamd_client=_get_clamd(), max_dim=AVATAR_MAX_DIM)
    key = storage.upload(user.id, cleaned.data, cleaned.content_type)
    user.avatar_url = storage.get_url(key)
    await repo.upsert(user)
    return {"avatar_url": storage.presigned_get_url(key)}
