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
    """A user's membership of an investigation, as a single linear ``role``:
    viewer < contributor < admin < owner. Owners are the special tier: they can
    delete the investigation and grant/revoke owner; an owner cannot change or
    remove another owner, and there is always >= 1 owner. The role also confers
    access to the investigation's contained articles/dossiers/viz (inheritance).
    """

    investigation_id: str
    user_id: str
    role: str = "viewer"
