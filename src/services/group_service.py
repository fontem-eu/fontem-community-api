"""Group service — the single place group state can change.

Closes the IDOR documented in the 2026-06-11 security review:
previously the groups router called the repository directly with no
authorization check, letting any authenticated user add or remove
anyone from any group. Every mutating method here takes the
calling user's id, hands it (plus the resolved group) to the
AuthorizationService, and only then mutates state.

Read methods (``get_by_id``, ``list_members``) require the caller
to have permission to see the resource at all; the policy enforces
that ``GROUPS_READ_MEMBERS`` is owner-only.
"""
from __future__ import annotations

from src.domain.group import Group
from src.repositories.group_repository import GroupRepository
from src.repositories.user_repository import UserRepository
from src.services.authz import (
    Action,
    AuthorizationService,
    ResourceRef,
)
from src.services.exceptions import InvalidInput, NotFound


class GroupService:
    def __init__(
        self,
        groups: GroupRepository,
        users: UserRepository,
        authz: AuthorizationService,
    ) -> None:
        self._groups = groups
        self._users = users
        self._authz = authz

    async def create(
        self,
        user_id: str,
        name: str,
        description: str = "",
    ) -> Group:
        """Create a new group owned by ``user_id``.

        Trust-level gated via the policy. The created group records
        ``created_by`` so subsequent membership ops can be ownership-
        gated.
        """
        principal = await self._authz.principal(user_id)
        await self._authz.require(principal, Action.GROUPS_CREATE)
        if not name.strip():
            raise InvalidInput("Group name cannot be empty")
        group = Group(name=name.strip(), description=description, created_by=user_id)
        return await self._groups.create(group)

    async def get(self, user_id: str | None, group_id: str) -> Group:
        """Get a group's name/description.

        Public-tier read — any authenticated user can see that a group
        exists and what it's called. The member list is a separate
        owner-only read (see :meth:`list_members`).
        """
        principal = await self._authz.principal(user_id)
        await self._authz.require(principal, Action.GROUPS_READ)
        group = await self._groups.get_by_id(group_id)
        if group is None:
            raise NotFound(f"Group {group_id} not found")
        return group

    async def list_members(self, user_id: str, group_id: str) -> list[str]:
        """Return the member user-ids of ``group_id``.

        Owner-only. Non-owners trying to read the membership list get
        a clean 403 from the policy.
        """
        group = await self._groups.get_by_id(group_id)
        if group is None:
            raise NotFound(f"Group {group_id} not found")
        principal = await self._authz.principal(user_id)
        await self._authz.require(
            principal, Action.GROUPS_READ_MEMBERS, ResourceRef.for_group(group),
        )
        return await self._groups.get_members(group_id)

    async def add_member(
        self, user_id: str, group_id: str, target_user_id: str,
    ) -> None:
        """Add ``target_user_id`` to ``group_id``.

        Only the creator (or an admin) can manage members — this is
        the call that the original IDOR exploited. Also validates that
        the target user actually exists so a non-existent user_id
        produces a clean 404 rather than a Postgres FK 500.
        """
        group = await self._groups.get_by_id(group_id)
        if group is None:
            raise NotFound(f"Group {group_id} not found")
        principal = await self._authz.principal(user_id)
        await self._authz.require(
            principal, Action.GROUPS_MANAGE_MEMBERS, ResourceRef.for_group(group),
        )
        target = await self._users.get_by_id(target_user_id)
        if target is None:
            raise NotFound(f"User {target_user_id} not found")
        await self._groups.add_member(group_id, target_user_id)

    async def remove_member(
        self, user_id: str, group_id: str, target_user_id: str,
    ) -> None:
        """Remove ``target_user_id`` from ``group_id``.

        Owner-gated. The legitimate creator can remove anyone; an
        attacker who managed to slip in via the old IDOR path
        cannot remove the creator (because they're not the creator).
        """
        group = await self._groups.get_by_id(group_id)
        if group is None:
            raise NotFound(f"Group {group_id} not found")
        principal = await self._authz.principal(user_id)
        await self._authz.require(
            principal, Action.GROUPS_MANAGE_MEMBERS, ResourceRef.for_group(group),
        )
        await self._groups.remove_member(group_id, target_user_id)
