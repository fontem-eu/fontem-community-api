"""Listing every account, for administrators only.

The directory is every user's email address next to when they sign in and
what they do, so it has an action of its own rather than borrowing a broader
one: `admin:users_list` — admin only, email confirmed, and, like every
require(), written to the authorisation audit log whether it is allowed or
refused. Who has looked at the user list is itself worth being able to
answer.
"""
from __future__ import annotations

from src.domain.user_directory import DIRECTORY_SORTS, MAX_PAGE_SIZE, UserDirectoryPage
from src.repositories.user_directory_repository import UserDirectoryRepository
from src.services.authz import Action, AuthorizationService
from src.services.exceptions import InvalidInput


class UserDirectoryService:
    def __init__(self, directory: UserDirectoryRepository, authz: AuthorizationService) -> None:
        self._directory = directory
        self._authz = authz

    async def list_users(
        self, admin_id: str, *, sort: str = "registered", limit: int = 50, offset: int = 0,
    ) -> UserDirectoryPage:
        # Authorisation first. The router already bounds these arguments, but
        # the service is the gate for any caller, and one who may not read the
        # directory should be refused before their arguments are judged.
        principal = await self._authz.principal(admin_id)
        await self._authz.require(principal, Action.ADMIN_USERS_LIST)
        if sort not in DIRECTORY_SORTS:
            raise InvalidInput(f"sort must be one of: {', '.join(DIRECTORY_SORTS)}")
        if not 1 <= limit <= MAX_PAGE_SIZE or offset < 0:
            raise InvalidInput(f"limit must be between 1 and {MAX_PAGE_SIZE}, offset at least 0")
        entries, total = await self._directory.list_users(sort=sort, limit=limit, offset=offset)
        return UserDirectoryPage(
            entries=entries, total=total, limit=limit, offset=offset, sort=sort,
        )
