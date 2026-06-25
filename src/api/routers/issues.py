from __future__ import annotations

from dataclasses import asdict
from typing import Annotated

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from src.api.auth import get_current_user
from src.api.openapi_responses import RESOURCE_RESPONSES, UuidPath
from src.domain.user import User
from src.services.exceptions import NotFound
from src.services.issue_service import IssueService

router = APIRouter(prefix="/issues", tags=["issues"], responses=RESOURCE_RESPONSES)


class CreateIssueRequest(BaseModel):
    title: str
    body: str = ""
    # incorrect_data, duplicate_entity, missing_connection, missing_entity, other
    issue_type: str = "other"
    # entity_type/entity_id are optional — a general issue (not tied to a specific
    # entity) is allowed; "" is the model's no-entity sentinel (NOT NULL default '').
    entity_type: str = ""
    entity_id: str = ""


class UpdateStatusRequest(BaseModel):
    status: str  # open, under_review, resolved, rejected, closed


class AddCommentRequest(BaseModel):
    body: str


class VoteRequest(BaseModel):
    direction: str  # up, down


@router.post("", status_code=201)
@inject
async def create_issue(
    body: CreateIssueRequest,
    *,
    svc: FromDishka[IssueService],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    issue = await svc.create(
        user.id, body.title, body.body, body.issue_type, body.entity_type, body.entity_id
    )
    return asdict(issue)


@router.get("")
@inject
# pylint: disable-next=too-many-arguments,too-many-positional-arguments
async def list_issues(
    *,
    entity_type: Annotated[str | None, Query()] = None,
    entity_id: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=2**31 - 1)] = 0,
    svc: FromDishka[IssueService],
    _user: Annotated[User, Depends(get_current_user)],
) -> list[dict]:
    if entity_type and entity_id:
        issues = await svc.list_for_entity(entity_type, entity_id, limit, offset)
    else:
        issues = await svc.list_open(limit, offset)
    return [asdict(i) for i in issues]


@router.get("/{issue_id}")
@inject
async def get_issue(
    issue_id: UuidPath,
    *,
    svc: FromDishka[IssueService],
    _user: Annotated[User, Depends(get_current_user)],
) -> dict:
    # ``svc._issues`` is the canonical repo handle. The service layer
    # exposes higher-level operations (create/resolve/comment) but
    # there's no dedicated "fetch one for read" verb — get_by_id is
    # what every consumer needs, so we reach through. Documented seam.
    issue = await svc._issues.get_by_id(issue_id)  # pylint: disable=protected-access
    if issue is None:
        raise NotFound(f"Issue {issue_id} not found")
    return asdict(issue)


@router.put("/{issue_id}/status")
@inject
async def update_status(
    issue_id: UuidPath,
    body: UpdateStatusRequest,
    *,
    svc: FromDishka[IssueService],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    await svc.resolve(user.id, issue_id, body.status)
    return {"status": "ok"}


@router.post("/{issue_id}/comments", status_code=201)
@inject
async def add_comment(
    issue_id: UuidPath,
    body: AddCommentRequest,
    *,
    svc: FromDishka[IssueService],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    comment = await svc.add_comment(user.id, issue_id, body.body)
    return asdict(comment)


@router.post("/{issue_id}/vote")
@inject
async def vote(
    issue_id: UuidPath,
    body: VoteRequest,
    *,
    svc: FromDishka[IssueService],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    await svc.vote(user.id, issue_id, body.direction)
    return {"status": "ok"}
