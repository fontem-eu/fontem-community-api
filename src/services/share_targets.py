"""Resolve a share/invite target to a user id WITHOUT leaking which emails are
registered.

Share-by-email endpoints used to raise NotFound when the email didn't match a
user, while succeeding when it did — a user-enumeration oracle (any signed-in
user could probe the whole user base). These helpers return ``None`` for an
unknown target instead, so callers respond uniformly (grant the existing user,
silently no-op for an unknown one) and an attacker can't tell the difference.
Only a genuinely malformed request (neither id nor email) is an error.
"""
from __future__ import annotations

from src.repositories.user_repository import UserRepository
from src.services.exceptions import InvalidInput


async def resolve_share_target(
    users: UserRepository,
    target_user_id: str | None,
    target_email: str | None,
) -> str | None:
    """User id for the target, or None if it doesn't resolve (uniform, no leak)."""
    if target_user_id:
        u = await users.get_by_id(target_user_id)
    elif target_email:
        u = await users.get_by_email(target_email.strip().lower())
    else:
        raise InvalidInput("must supply target user_id or email")
    return u.id if u is not None else None
