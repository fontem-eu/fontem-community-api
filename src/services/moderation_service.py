from __future__ import annotations

from datetime import datetime

from src.domain.moderation import Flag, Sanction
from src.repositories.moderation_repository import ModerationRepository
from src.repositories.user_repository import UserRepository
from src.services.exceptions import Conflict, PermissionDenied

AUTO_HIDE_THRESHOLD = 3

TRUST_LEVELS = ["new_user", "commenter", "contributor", "moderator", "admin"]


class ModerationService:
    def __init__(self, mod: ModerationRepository, users: UserRepository) -> None:
        self._mod = mod
        self._users = users

    def _trust_rank(self, level: str) -> int:
        try:
            return TRUST_LEVELS.index(level)
        except ValueError:
            return 0

    async def _require_moderator(self, user_id: str) -> None:
        user = await self._users.get_by_id(user_id)
        if user is None:
            raise PermissionDenied("User not found")
        roles = await self._users.get_roles(user_id)
        is_mod = (
            "moderator" in roles
            or "admin" in roles
            or self._trust_rank(user.trust_level) >= self._trust_rank("moderator")
        )
        if not is_mod:
            raise PermissionDenied("Moderator role required")

    async def _require_admin(self, user_id: str) -> None:
        user = await self._users.get_by_id(user_id)
        if user is None:
            raise PermissionDenied("User not found")
        roles = await self._users.get_roles(user_id)
        if "admin" not in roles and user.trust_level != "admin":
            raise PermissionDenied("Admin role required")

    async def flag(
        self,
        user_id: str,
        target_type: str,
        target_id: str,
        reason: str,
        details: str = "",
    ) -> Flag:
        already = await self._mod.has_flagged(target_type, target_id, user_id)
        if already:
            raise Conflict("You have already flagged this content")
        flag = Flag(
            target_type=target_type,
            target_id=target_id,
            reason=reason,
            details=details,
            flagged_by=user_id,
        )
        flag = await self._mod.add_flag(flag)
        count = await self._mod.count_flags(target_type, target_id)
        if count >= AUTO_HIDE_THRESHOLD:
            # For now, just return the flag; auto-hide logic will be added later
            pass
        return flag

    async def sanction(
        self,
        moderator_id: str,
        user_id: str,
        type: str,
        reason: str,
        expires_at: datetime | None = None,
    ) -> Sanction:
        if type == "ban":
            await self._require_admin(moderator_id)
        else:
            await self._require_moderator(moderator_id)
        s = Sanction(
            user_id=user_id,
            type=type,
            reason=reason,
            expires_at=expires_at,
            applied_by=moderator_id,
        )
        result = await self._mod.add_sanction(s)
        await self._users.add_sanction(result)
        return result

    async def lift(self, moderator_id: str, sanction_id: str) -> None:
        await self._require_moderator(moderator_id)
        await self._mod.lift_sanction(sanction_id)
        await self._users.lift_sanction(sanction_id)

    async def get_queue(self, moderator_id: str, limit: int, offset: int) -> list[Flag]:
        await self._require_moderator(moderator_id)
        return await self._mod.list_flagged(limit, offset)

    async def resolve_flags(
        self, moderator_id: str, target_type: str, target_id: str, action: str
    ) -> None:
        await self._require_moderator(moderator_id)
        await self._mod.resolve_flags(target_type, target_id, action, moderator_id)

    async def get_log(self, moderator_id: str, limit: int, offset: int) -> list[dict]:
        await self._require_moderator(moderator_id)
        return await self._mod.get_log(limit, offset)
