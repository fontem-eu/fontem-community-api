"""Briefings — the public surface, watching, and Atom feeds.

Three kinds of route here, and the difference matters:

* ``GET /briefings*`` is anonymous. It is a catalogue of public records; a
  visitor should be able to see what a briefing contains before deciding
  whether to watch it, and before deciding whether to have an account.
* ``/me/watches*`` needs a session. A watch is personal data.
* ``GET /feeds/{token}.atom`` is anonymous but unguessable, because Atom
  readers cannot authenticate. The token names the watch and nothing else.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Annotated

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field

from src.api.auth import get_current_user
from src.api.openapi_responses import RESOURCE_RESPONSES, UuidPath
from src.domain.feed import DEFAULT_VOLUME, MAX_VOLUME, MIN_VOLUME
from src.domain.user import User
from src.services import atom
from src.services.briefing_service import BriefingService

router = APIRouter(tags=["briefings"], responses=RESOURCE_RESPONSES)

SITE_URL = "https://fontem.eu"


class WatchRequest(BaseModel):
    nuts: list[str] = Field(default_factory=lambda: ["EU"], max_length=60)
    volume_per_week: int = Field(default=DEFAULT_VOLUME, ge=MIN_VOLUME, le=MAX_VOLUME)


def _item_json(item) -> dict:
    out = asdict(item)
    for key in ("item_time", "first_seen_at"):
        if out.get(key) is not None:
            out[key] = getattr(item, key).isoformat()
    return out


def _briefing_json(group) -> dict:
    return {
        "slug": group.slug,
        "name": group.name,
        "description": group.description,
        "queries": [{"slug": q.slug, "name": q.name, "description": q.description}
                    for q in group.queries],
    }


# ── public catalogue ────────────────────────────────────────────
@router.get("/briefings", openapi_extra={"security": []})
@inject
async def list_briefings(*, svc: FromDishka[BriefingService]) -> list[dict]:
    return [_briefing_json(g) for g in await svc.list_briefings()]


@router.get("/briefings/{slug}", openapi_extra={"security": []})
@inject
async def get_briefing(
    *,
    svc: FromDishka[BriefingService],
    slug: str,
    nuts: Annotated[list[str] | None, Query()] = None,
    volume: Annotated[int, Query(ge=MIN_VOLUME, le=MAX_VOLUME)] = DEFAULT_VOLUME,
) -> dict:
    """The briefing, plus what it currently holds for these regions.

    Anonymous on purpose: deciding whether a briefing is worth watching
    requires seeing what is in it.
    """
    group = await svc.get_briefing(slug)
    items = await svc.preview(slug, nuts, volume)
    return {**_briefing_json(group), "items": [_item_json(i) for i in items]}


# ── watching ────────────────────────────────────────────────────
@router.put("/briefings/{slug}/watch")
@inject
async def watch_briefing(
    *,
    svc: FromDishka[BriefingService],
    user: Annotated[User, Depends(get_current_user)],
    slug: str,
    body: WatchRequest,
) -> dict:
    """Watch a briefing, or adjust a watch already in place.

    PUT rather than POST because it is idempotent by (user, briefing):
    watching twice adjusts, it does not mint a second feed URL.
    """
    watch = await svc.watch(user.id, slug, body.nuts, body.volume_per_week)
    return _watch_json(watch, slug)


@router.get("/me/watches")
@inject
async def list_my_watches(
    *,
    svc: FromDishka[BriefingService],
    user: Annotated[User, Depends(get_current_user)],
) -> list[dict]:
    return [_watch_json(w) for w in await svc.list_watches(user.id)]


@router.delete("/me/watches/{watch_id}", status_code=204)
@inject
async def unwatch(
    *,
    svc: FromDishka[BriefingService],
    user: Annotated[User, Depends(get_current_user)],
    watch_id: UuidPath,
) -> None:
    await svc.unwatch(user.id, watch_id)


def _watch_json(watch, slug: str | None = None) -> dict:
    out = {
        "id": watch.id,
        "group_id": watch.group_id,
        "nuts": watch.nuts,
        "volume_per_week": watch.volume_per_week,
        "feed_url": f"{SITE_URL}/capi/feeds/{watch.token}.atom",
        "created_at": watch.created_at.isoformat() if watch.created_at else None,
        "last_polled_at": watch.last_polled_at.isoformat() if watch.last_polled_at else None,
    }
    if slug:
        out["slug"] = slug
    return out


# ── the feed ────────────────────────────────────────────────────
# No return annotation: `from __future__ import annotations` turns it into a
# ForwardRef that FastAPI tries to build a response model from, and a raw
# Response has no model. response_class is what documents it instead.
@router.get("/feeds/{token}.atom", openapi_extra={"security": []},
            response_class=Response)
@inject
async def atom_feed(
    *,
    svc: FromDishka[BriefingService],
    request: Request,
    token: str,
):
    """The Atom document for one watch.

    Conditional GET is honoured, because a reader polling every 15 minutes
    should cost a 304 and not a rendered document. The ETag is weak and
    derived from the items, so it changes exactly when the content does.
    """
    watch, group, items = await svc.feed_for_token(token)
    etag = atom.etag_for(items)
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})

    regions = ", ".join(watch.nuts)
    body = atom.render(
        title=f"{group.name} — Fontem",
        subtitle=f"{group.description} Regions: {regions}."
                 if group.description else f"Regions: {regions}.",
        feed_url=f"{SITE_URL}/capi/feeds/{token}.atom",
        site_url=SITE_URL,
        items=items,
    )
    return Response(
        content=body,
        media_type=atom.CONTENT_TYPE,
        headers={
            "ETag": etag,
            # The runner's cadence is the floor on how often this can change.
            "Cache-Control": "public, max-age=900",
            # The token is a bearer secret in a URL; keep it out of referrers.
            "Referrer-Policy": "no-referrer",
        },
    )
