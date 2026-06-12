from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class Group:
    """Group of users. Reports/stories can be shared with a group;
    every member gets the granted access level on the report.

    ``created_by`` is the anchor for the AuthorizationService — the
    creator is the only one who can manage the membership list,
    delete the group, etc. (see src/services/authz/policy.py for the
    full policy). Nullable to support legacy rows that pre-date the
    authz service rollout; the policy treats those as admin-only.
    """

    id: str | None = None
    name: str = ""
    description: str = ""
    created_by: str | None = None
    created_at: datetime | None = None
