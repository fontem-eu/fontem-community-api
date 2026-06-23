from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Visualization:
    """A saved visualization — the server-side successor to the localStorage
    "pocket". A viz is the *recipe* (``widget_type`` + ``config`` JSON) the
    client re-renders the chart from, not a rendered image. Owned by
    ``created_by``; optionally attached to an investigation
    (``investigation_id``) so a team sees it under that investigation and it can
    be inserted into the investigation's articles.
    """

    id: str | None = None
    name: str = ""
    widget_type: str = ""
    config: dict = field(default_factory=dict)
    created_by: str | None = None
    investigation_id: str | None = None
    created_at: datetime | None = None
