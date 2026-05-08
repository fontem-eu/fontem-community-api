"""Tag endpoints — story tags + per-user followed tags + browse."""
from __future__ import annotations

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.api.auth import get_current_user
from src.api.openapi_responses import AUTH_RESPONSES
from src.domain.user import User
from src.services.exceptions import InvalidInput, NotFound, PermissionDenied
from src.services.tag_service import (
    MAX_FOLLOWED_TAGS_PER_USER,
    MAX_TAGS_PER_STORY,
    TagService,
)


router = APIRouter(tags=["tags"], responses=AUTH_RESPONSES)


# ── Story tags ────────────────────────────────────────────────


class SetStoryTagsRequest(BaseModel):
    """Payload for ``PUT /data-stories/{id}/tags``.

    Free-text input (the editor sends what the user typed). The
    service normalises to slugs and enforces the ≤3 limit.
    """
    tags: list[str] = Field(..., max_length=20)
    # Cap the *raw* input list at 20 so a malformed client can't
    # spam huge bodies — the service still rejects >MAX_TAGS_PER_STORY
    # after dedupe / normalise, but this is the cheap pre-filter.


@router.put(
    "/data-stories/{report_id}/tags",
    summary="Replace the tag set for a story (owner only)",
)
@inject
async def put_story_tags(
    report_id: str,
    body: SetStoryTagsRequest,
    *,
    svc: FromDishka[TagService],
    user: User = Depends(get_current_user),
) -> dict:
    try:
        tags = await svc.set_story_tags(user.id, report_id, body.tags)
    except NotFound as e:
        raise HTTPException(status_code=404, detail=e.message) from e
    except PermissionDenied as e:
        raise HTTPException(status_code=403, detail=e.message) from e
    except InvalidInput as e:
        raise HTTPException(status_code=400, detail=e.message) from e
    return {"tags": tags}


@router.get(
    "/tags",
    summary="List every tag in use on public stories with story counts",
)
@inject
async def list_tags(*, svc: FromDishka[TagService]) -> dict:
    """Public, unauthenticated. Powers the feed's browse-by-tag chip
    strip — a small list (low thousands at most) sorted by descending
    use count then alphabetical.
    """
    tags = await svc.list_distinct_tags()
    return {
        "tags": [{"tag": t, "story_count": n} for t, n in tags],
        "limits": {
            "max_per_story": MAX_TAGS_PER_STORY,
            "max_followed_per_user": MAX_FOLLOWED_TAGS_PER_USER,
        },
    }


# ── Followed tags (auth users) ────────────────────────────────


class FollowTagRequest(BaseModel):
    tag: str = Field(..., min_length=1, max_length=80)
    # Loose cap on the raw input — server slug-normalises and length-
    # caps to MAX_LEN (40) before insert.


@router.get("/me/followed-tags", summary="List the user's followed tags")
@inject
async def list_followed_tags(
    *,
    svc: FromDishka[TagService],
    user: User = Depends(get_current_user),
) -> dict:
    tags = await svc.list_followed(user.id)
    return {"tags": tags}


@router.post(
    "/me/followed-tags",
    status_code=201,
    summary="Follow a tag",
)
@inject
async def follow_tag(
    body: FollowTagRequest,
    *,
    svc: FromDishka[TagService],
    user: User = Depends(get_current_user),
) -> dict:
    try:
        slug = await svc.follow(user.id, body.tag)
    except InvalidInput as e:
        raise HTTPException(status_code=400, detail=e.message) from e
    return {"tag": slug}


@router.delete(
    "/me/followed-tags/{tag}",
    status_code=204,
    summary="Unfollow a tag",
)
@inject
async def unfollow_tag(
    tag: str,
    *,
    svc: FromDishka[TagService],
    user: User = Depends(get_current_user),
) -> None:
    try:
        await svc.unfollow(user.id, tag)
    except InvalidInput as e:
        raise HTTPException(status_code=400, detail=e.message) from e
    return None
