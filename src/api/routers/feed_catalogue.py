"""Admin endpoints for the feed-query catalogue, plus the public picker read.

Thin handlers over :class:`NamedQueryService`. Every write is admin-gated
through ``AuthorizationService`` inside the service, so the gate is in one
place and lands in the audit log rather than being re-implemented per route.

``GET /query-groups`` is the one anonymous route: it is what the feed picker
renders, and it exposes only published queries in public groups.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Annotated

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from src.api.auth import get_current_user
from src.api.openapi_responses import RESOURCE_RESPONSES, UuidPath
from src.domain.named_query import NamedQuery, QueryGroup
from src.domain.user import User
from src.services.named_query_service import NamedQueryService

router = APIRouter(tags=["feed-catalogue"], responses=RESOURCE_RESPONSES)


# ── request models ──────────────────────────────────────────────
class CreateNamedQueryRequest(BaseModel):
    slug: str = Field(min_length=1, max_length=120)
    name: str = Field(default="", max_length=300)
    description: str = Field(default="", max_length=4000)
    lang: str = Field(default="sql", max_length=20)
    # 8000 rather than the proxy's 8192 so a body that only just fits here
    # can still gain the few bytes of whitespace the editor adds.
    query: str = Field(default="", max_length=8000)
    params: list[dict] = Field(default_factory=list)
    waivers: dict[str, str] = Field(default_factory=dict)


class UpdateNamedQueryRequest(BaseModel):
    slug: str | None = Field(default=None, max_length=120)
    name: str | None = Field(default=None, max_length=300)
    description: str | None = Field(default=None, max_length=4000)
    lang: str | None = Field(default=None, max_length=20)
    query: str | None = Field(default=None, max_length=8000)
    params: list[dict] | None = None
    waivers: dict[str, str] | None = None
    status: str | None = Field(default=None, max_length=20)


class PreviewRequest(BaseModel):
    lang: str = Field(default="sql", max_length=20)
    query: str = Field(default="", max_length=8000)
    params: dict = Field(default_factory=dict)
    waivers: dict[str, str] = Field(default_factory=dict)


class CreateQueryGroupRequest(BaseModel):
    slug: str = Field(min_length=1, max_length=120)
    name: str = Field(default="", max_length=300)
    description: str = Field(default="", max_length=4000)
    sort_order: int = 0
    visibility: str = Field(default="public", max_length=20)


class UpdateQueryGroupRequest(BaseModel):
    slug: str | None = Field(default=None, max_length=120)
    name: str | None = Field(default=None, max_length=300)
    description: str | None = Field(default=None, max_length=4000)
    sort_order: int | None = None
    visibility: str | None = Field(default=None, max_length=20)


class SetGroupQueriesRequest(BaseModel):
    query_ids: list[str] = Field(default_factory=list)


# ── serialisation ───────────────────────────────────────────────
def _query_json(query: NamedQuery) -> dict:
    out = asdict(query)
    report = out.get("contract_report")
    if report and report.get("checked_at") is not None:
        report["checked_at"] = query.contract_report.checked_at.isoformat()
    return out


def _group_json(group: QueryGroup) -> dict:
    out = asdict(group)
    out["queries"] = [_query_json(q) for q in group.queries]
    return out


# ── named queries ───────────────────────────────────────────────
@router.get("/admin/named-queries")
@inject
async def list_named_queries(
    *,
    svc: FromDishka[NamedQueryService],
    user: Annotated[User, Depends(get_current_user)],
    status: Annotated[str | None, Query()] = None,
) -> list[dict]:
    return [_query_json(q) for q in await svc.list_queries(user.id, status)]


@router.post("/admin/named-queries", status_code=201)
@inject
async def create_named_query(
    *,
    svc: FromDishka[NamedQueryService],
    user: Annotated[User, Depends(get_current_user)],
    body: CreateNamedQueryRequest,
) -> dict:
    return _query_json(await svc.create_query(user.id, **body.model_dump()))


@router.get("/admin/named-queries/{query_id}")
@inject
async def get_named_query(
    *,
    svc: FromDishka[NamedQueryService],
    user: Annotated[User, Depends(get_current_user)],
    query_id: UuidPath,
) -> dict:
    query = await svc.get_query(user.id, query_id)
    out = _query_json(query)
    # The per-query panel always shows which groups it is in; folding it into
    # the read saves the UI a second round trip on every selection.
    out["groups"] = [
        {"id": g.id, "slug": g.slug, "name": g.name}
        for g in await svc.groups_for_query(user.id, query_id)
    ]
    return out


@router.patch("/admin/named-queries/{query_id}")
@inject
async def update_named_query(
    *,
    svc: FromDishka[NamedQueryService],
    user: Annotated[User, Depends(get_current_user)],
    query_id: UuidPath,
    body: UpdateNamedQueryRequest,
) -> dict:
    fields = body.model_dump(exclude_unset=True)
    return _query_json(await svc.update_query(user.id, query_id, **fields))


@router.delete("/admin/named-queries/{query_id}", status_code=204)
@inject
async def delete_named_query(
    *,
    svc: FromDishka[NamedQueryService],
    user: Annotated[User, Depends(get_current_user)],
    query_id: UuidPath,
) -> None:
    await svc.delete_query(user.id, query_id)


@router.post("/admin/named-queries/{query_id}/validate")
@inject
async def validate_named_query(
    *,
    svc: FromDishka[NamedQueryService],
    user: Annotated[User, Depends(get_current_user)],
    query_id: UuidPath,
) -> dict:
    return _query_json(await svc.validate_query(user.id, query_id))


@router.post("/admin/named-queries/preview")
@inject
async def preview_named_query(
    *,
    svc: FromDishka[NamedQueryService],
    user: Annotated[User, Depends(get_current_user)],
    body: PreviewRequest,
) -> dict:
    draft = NamedQuery(lang=body.lang, query=body.query, waivers=dict(body.waivers))
    out = await svc.preview(user.id, draft, params=body.params)
    contract = out["contract"]
    out["contract"] = {
        **asdict(contract),
        "checked_at": contract.checked_at.isoformat() if contract.checked_at else None,
    }
    return out


# ── query groups ────────────────────────────────────────────────
@router.get("/admin/query-groups")
@inject
async def list_query_groups(
    *,
    svc: FromDishka[NamedQueryService],
    user: Annotated[User, Depends(get_current_user)],
) -> list[dict]:
    return [_group_json(g) for g in await svc.list_groups(user.id)]


@router.post("/admin/query-groups", status_code=201)
@inject
async def create_query_group(
    *,
    svc: FromDishka[NamedQueryService],
    user: Annotated[User, Depends(get_current_user)],
    body: CreateQueryGroupRequest,
) -> dict:
    return _group_json(await svc.create_group(user.id, **body.model_dump()))


@router.get("/admin/query-groups/{group_id}")
@inject
async def get_query_group(
    *,
    svc: FromDishka[NamedQueryService],
    user: Annotated[User, Depends(get_current_user)],
    group_id: UuidPath,
) -> dict:
    return _group_json(await svc.get_group(user.id, group_id))


@router.patch("/admin/query-groups/{group_id}")
@inject
async def update_query_group(
    *,
    svc: FromDishka[NamedQueryService],
    user: Annotated[User, Depends(get_current_user)],
    group_id: UuidPath,
    body: UpdateQueryGroupRequest,
) -> dict:
    fields = body.model_dump(exclude_unset=True)
    return _group_json(await svc.update_group(user.id, group_id, **fields))


@router.delete("/admin/query-groups/{group_id}", status_code=204)
@inject
async def delete_query_group(
    *,
    svc: FromDishka[NamedQueryService],
    user: Annotated[User, Depends(get_current_user)],
    group_id: UuidPath,
) -> None:
    await svc.delete_group(user.id, group_id)


@router.put("/admin/query-groups/{group_id}/queries")
@inject
async def set_query_group_queries(
    *,
    svc: FromDishka[NamedQueryService],
    user: Annotated[User, Depends(get_current_user)],
    group_id: UuidPath,
    body: SetGroupQueriesRequest,
) -> dict:
    """Replace the group's membership, in the order given.

    Replace-the-whole-set rather than add/remove, because the UI edits an
    ordered list and expressing a positional edit as a diff is where ordering
    bugs live.
    """
    return _group_json(await svc.set_group_queries(user.id, group_id, body.query_ids))


# ── public ──────────────────────────────────────────────────────
@router.get("/query-groups", openapi_extra={"security": []})
@inject
async def public_query_groups(*, svc: FromDishka[NamedQueryService]) -> list[dict]:
    """Published queries in public groups — what the feed picker renders.

    Anonymous by design; a group with no published queries is omitted rather
    than shown empty.
    """
    return [_group_json(g) for g in await svc.public_catalogue()]
