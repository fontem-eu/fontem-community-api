from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class Dossier:
    """A thin tree-of-articles (Confluence-style) structuring construct.

    Articles (``reports``) are placed in a dossier via ``reports.dossier_id``
    and arranged into a tree via the existing ``reports.parent_id``. A dossier
    optionally belongs to an investigation (``investigation_id``); standalone
    dossiers (investigation_id None) are owned by ``created_by``. Permissions
    on the articles themselves stay on the articles (report_access) — a dossier
    only structures them.
    """

    id: str | None = None
    name: str = ""
    investigation_id: str | None = None
    created_by: str | None = None
    created_at: datetime | None = None
