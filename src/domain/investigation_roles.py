"""Investigation membership roles — a single linear role replaces the old
capability-flag grid. Also the inheritance map (role -> report access level)
used when an investigation confers access to its contained articles/dossiers/viz.
"""
from __future__ import annotations

ROLES = ("viewer", "contributor", "admin", "owner")
ROLE_RANK: dict[str, int] = {r: i for i, r in enumerate(ROLES)}

# Investigation role -> report_access level on contained resources (Phase B).
ROLE_TO_LEVEL: dict[str, str] = {
    "viewer": "viewer",
    "contributor": "editor",
    "admin": "editor",
    "owner": "owner",
}


def is_role(value: str | None) -> bool:
    return value in ROLE_RANK


def role_at_least(role: str | None, minimum: str) -> bool:
    """True if ``role`` is at or above ``minimum`` in the hierarchy."""
    return ROLE_RANK.get(role or "", -1) >= ROLE_RANK[minimum]


def role_from_flags(
    can_write_stories: bool, can_add_viz: bool, can_administer: bool, is_owner: bool,
) -> str:
    """Collapse the legacy capability flags into a role (migration helper)."""
    if is_owner:
        return "owner"
    if can_administer:
        return "admin"
    if can_write_stories or can_add_viz:
        return "contributor"
    return "viewer"
