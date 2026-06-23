from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


# ── domain dataclass: each attribute is a column on the reports table.
@dataclass
class Report:  # pylint: disable=too-many-instance-attributes
    id: str | None = None
    title: str = ""
    abstract: str | None = None
    visibility: str = "private"  # private, group, public_auth, public_open
    parent_id: str | None = None  # tree position within a dossier (None = root)
    dossier_id: str | None = None  # which dossier this article belongs to
    investigation_id: str | None = None  # direct investigation link (M4)
    created_by: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class Section:
    id: str | None = None
    report_id: str = ""
    sort_order: int = 0
    content_json: dict = field(default_factory=dict)
    lock_holder: str | None = None
    lock_expires: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class SectionVersion:
    id: str | None = None
    section_id: str = ""
    content_json: dict = field(default_factory=dict)
    saved_by: str = ""
    saved_at: datetime | None = None


@dataclass
class AccessGrant:
    id: str | None = None
    report_id: str = ""
    user_id: str | None = None
    group_id: str | None = None
    level: str = "viewer"  # owner, editor, commenter, viewer
