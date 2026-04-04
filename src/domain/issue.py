from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class Issue:
    id: str | None = None
    title: str = ""
    body_md: str = ""
    issue_type: str = "other"  # incorrect_data, duplicate_entity, missing_connection, missing_entity, other
    entity_type: str = ""
    entity_id: str = ""
    status: str = "open"  # open, under_review, resolved, rejected, closed
    created_by: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class Comment:
    id: str | None = None
    parent_type: str = ""  # report, issue
    parent_id: str = ""
    body_md: str = ""
    author_id: str = ""
    created_at: datetime | None = None
