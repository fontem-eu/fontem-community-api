from __future__ import annotations

from src.repositories.group_repository import GroupRepository
from src.repositories.permission_repository import PermissionRepository
from src.repositories.user_repository import UserRepository
from src.services.exceptions import PermissionDenied

LEVEL_HIERARCHY: dict[str, int] = {
    "viewer": 0,
    "commenter": 1,
    "editor": 2,
    "owner": 3,
}


class PermissionService:
    def __init__(
        self,
        perms: PermissionRepository,
        users: UserRepository,
        groups: GroupRepository,
    ) -> None:
        self._perms = perms
        self._users = users
        self._groups = groups

    async def check(self, user_id: str, report_id: str, required_level: str) -> bool:
        # Admin override (check both trust_level and roles)
        roles = await self._users.get_roles(user_id)
        if "admin" in roles:
            return True
        user = await self._users.get_by_id(user_id)
        if user is not None and user.trust_level == "admin":
            return True

        # Sanction check
        sanction = await self._users.get_active_sanction(user_id)
        if sanction is not None and sanction.type in ("suspend", "ban"):
            return False

        # Check access level
        effective = await self._perms.get_report_access(user_id, report_id)
        if effective is None:
            # Check visibility for public reports
            visibility = await self._perms.get_report_visibility(report_id)
            if visibility in ("public_auth", "public_open") and required_level == "viewer":
                return True
            return False

        required_rank = LEVEL_HIERARCHY.get(required_level, 0)
        effective_rank = LEVEL_HIERARCHY.get(effective, 0)
        return effective_rank >= required_rank

    async def require(self, user_id: str, report_id: str, required_level: str) -> None:
        allowed = await self.check(user_id, report_id, required_level)
        if not allowed:
            raise PermissionDenied(
                f"User {user_id} lacks '{required_level}' access to report {report_id}"
            )

    async def grant_access(
        self, report_id: str, user_id: str, level: str = "owner",
    ) -> None:
        """Grant a user access to a report (used by ReportService on create)."""
        await self._perms.set_user_access(report_id, user_id, level)
