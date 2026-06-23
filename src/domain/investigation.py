from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class Investigation:
    """An aggregating workspace that groups dossiers, articles and
    visualizations and carries its own membership/permissions.

    ``created_by`` is the founding owner; membership + capabilities live
    in :class:`InvestigationMember`. Purely additive — articles
    (``reports``) keep their own ``report_access`` permissions; an
    investigation does not (yet) confer article access.
    """

    id: str | None = None
    name: str = ""
    description: str = ""
    created_by: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class InvestigationMember:
    """A user's membership of an investigation, as independent capability
    flags (not a linear role). ``is_owner`` is the special tier: owners can
    delete the investigation and promote/own; an owner cannot change or
    remove another owner, and there is always >= 1 owner."""

    investigation_id: str
    user_id: str
    can_write_stories: bool = False
    can_add_viz: bool = False
    can_administer: bool = False
    is_owner: bool = False
