"""Data Studio domain — a data project groups the user's saved queries and
plots. Owner-private; the query/plot recipes are re-runnable (no results are
stored server-side — DuckDB runs the combine in the browser)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class DataQuery:  # pylint: disable=too-many-instance-attributes
    id: str | None = None
    project_id: str = ""
    name: str = ""
    lang: str = "cypher"
    query: str = ""
    sort_order: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class DataPlot:
    id: str | None = None
    project_id: str = ""
    name: str = ""
    spec: dict = field(default_factory=dict)  # {sources, transform, chart, x, y}
    sort_order: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class DataProject:  # pylint: disable=too-many-instance-attributes
    id: str | None = None
    name: str = ""
    created_by: str = ""
    # When set, the project is attached to an investigation and its members
    # inherit access by role (viewer→read, contributor→edit, owner→own).
    investigation_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    queries: list[DataQuery] = field(default_factory=list)
    plots: list[DataPlot] = field(default_factory=list)
