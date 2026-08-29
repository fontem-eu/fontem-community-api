"""Tag endpoints — story tags + per-user followed tags + browse."""
from __future__ import annotations

from typing import Annotated

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field

from src.api.auth import get_current_user
from src.api.openapi_responses import RESOURCE_RESPONSES, UuidPath
from src.domain.user import User
from src.services.exceptions import InvalidInput, NotFound, PermissionDenied
from src.services.tag_service import (
    MAX_FOLLOWED_TAGS_PER_USER,
    MAX_TAGS_PER_STORY,
    TagService,
)


router = APIRouter(tags=["tags"], responses=RESOURCE_RESPONSES)


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
    responses={
        404: {"description": "Story not found."},
        403: {"description": "Caller is not the story owner."},
        400: {"description": "Tag normalisation rejected the input (slug shape / count)."},
    },
)
@inject
async def put_story_tags(
    report_id: UuidPath,
    body: SetStoryTagsRequest,
    *,
    svc: FromDishka[TagService],
    user: Annotated[User, Depends(get_current_user)],
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
    # Public, like the feed itself — tell OpenAPI consumers (e.g.
    # schemathesis) the operation has no security requirement. Without
    # this the 200 responses get flagged as "accepts requests without
    # authentication".
    openapi_extra={"security": []},
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
    # Require at least one alphanumeric so the normaliser doesn't 400
    # on "all-special-chars" inputs that collapse to empty after slug
    # normalisation. Server still does the full slug normalise +
    # MAX_LEN (40) cap; this just lifts the obvious rejects into the
    # OpenAPI shape so schemathesis stops generating gibberish that
    # the API correctly bounces with 400.
    tag: str = Field(..., min_length=1, max_length=80, pattern=r".*[A-Za-z0-9].*")


class FollowedTagsResponse(BaseModel):
    """The signed-in user's followed tag slugs."""

    tags: list[str]


@router.get("/me/followed-tags", summary="List the user's followed tags", response_model=FollowedTagsResponse)
@inject
async def list_followed_tags(
    *,
    svc: FromDishka[TagService],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    tags = await svc.list_followed(user.id)
    return {"tags": tags}


@router.post(
    "/me/followed-tags",
    status_code=201,
    summary="Follow a tag",
    responses={
        400: {"description": "Tag slug failed normalisation."},
    },
)
@inject
async def follow_tag(
    body: FollowTagRequest,
    *,
    svc: FromDishka[TagService],
    user: Annotated[User, Depends(get_current_user)],
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
    responses={
        400: {"description": "Tag slug failed normalisation."},
    },
)
@inject
async def unfollow_tag(
    # Slug-shaped path param. Same pattern as FollowTagRequest.tag but
    # tighter — by the time the caller unfollows, the value should be
    # the normalised slug (a-z, 0-9, hyphen).
    tag: Annotated[str, Path(pattern=r"^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$")],
    *,
    svc: FromDishka[TagService],
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    try:
        await svc.unfollow(user.id, tag)
    except InvalidInput as e:
        raise HTTPException(status_code=400, detail=e.message) from e
    return None
