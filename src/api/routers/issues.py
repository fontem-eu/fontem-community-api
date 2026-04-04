from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from src.api.auth import get_current_user
from src.api.dependencies import get_issue_service
from src.domain.user import User
from src.services.issue_service import IssueService

router = APIRouter(prefix="/issues", tags=["issues"])


class CreateIssueRequest(BaseModel):
    title: str
    body: str
    issue_type: str  # incorrect_data, duplicate_entity, missing_connection, missing_entity, other
    entity_type: str
    entity_id: str


class UpdateStatusRequest(BaseModel):
    status: str  # open, under_review, resolved, rejected, closed


class AddCommentRequest(BaseModel):
    body: str


class VoteRequest(BaseModel):
    direction: str  # up, down


@router.post("", status_code=201)
async def create_issue(
    body: CreateIssueRequest,
    user: User = Depends(get_current_user),
    svc: IssueService = Depends(get_issue_service),
) -> dict:
    issue = await svc.create(
        user.id, body.title, body.body, body.issue_type, body.entity_type, body.entity_id
    )
    return asdict(issue)


@router.get("")
async def list_issues(
    entity_type: str | None = Query(None),
    entity_id: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    svc: IssueService = Depends(get_issue_service),
) -> list[dict]:
    if entity_type and entity_id:
        issues = await svc.list_for_entity(entity_type, entity_id, limit, offset)
    else:
        issues = await svc.list_open(limit, offset)
    return [asdict(i) for i in issues]


@router.get("/{issue_id}")
async def get_issue(
    issue_id: str,
    user: User = Depends(get_current_user),
    svc: IssueService = Depends(get_issue_service),
) -> dict:
    from src.services.exceptions import NotFound

    issue = await svc._issues.get_by_id(issue_id)
    if issue is None:
        raise NotFound(f"Issue {issue_id} not found")
    return asdict(issue)


@router.put("/{issue_id}/status")
async def update_status(
    issue_id: str,
    body: UpdateStatusRequest,
    user: User = Depends(get_current_user),
    svc: IssueService = Depends(get_issue_service),
) -> dict:
    await svc.resolve(user.id, issue_id, body.status)
    return {"status": "ok"}


@router.post("/{issue_id}/comments", status_code=201)
async def add_comment(
    issue_id: str,
    body: AddCommentRequest,
    user: User = Depends(get_current_user),
    svc: IssueService = Depends(get_issue_service),
) -> dict:
    comment = await svc.add_comment(user.id, issue_id, body.body)
    return asdict(comment)


@router.post("/{issue_id}/vote")
async def vote(
    issue_id: str,
    body: VoteRequest,
    user: User = Depends(get_current_user),
    svc: IssueService = Depends(get_issue_service),
) -> dict:
    await svc.vote(user.id, issue_id, body.direction)
    return {"status": "ok"}
