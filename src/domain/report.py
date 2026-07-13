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
    # Optional NUTS region this story is about (any level, e.g. PT / PT17).
    nuts_region: str = ""
    parent_id: str | None = None  # tree position within a dossier (None = root)
    dossier_id: str | None = None  # which dossier this article belongs to
    investigation_id: str | None = None  # direct investigation link (M4)
    language: str = "en"  # language of the original text (ISO 639-1)
    # Monotonic counter bumped on every translatable change (document save,
    # title/abstract edit). Translations pin the version they were made
    # against; translation.source_version < content_version = maybe outdated.
    content_version: int = 1
    created_by: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class ReportTranslation:
    id: str | None = None
    report_id: str = ""
    lang: str = ""  # ISO 639-1 of the translation
    title: str = ""
    abstract: str | None = None
    content_json: dict = field(default_factory=dict)  # same v2 tiptap shape as sections
    source_version: int = 1  # report.content_version this translation tracked
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
