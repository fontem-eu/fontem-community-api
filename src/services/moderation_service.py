"""Moderation service — flags, sanctions, queue, log.

All policy decisions delegate to :class:`AuthorizationService` so the
moderation surface is auditable end-to-end. The ban-vs-other-sanction
split is encoded as two distinct actions (``SANCTIONS_BAN`` /
``SANCTIONS_CREATE``) — the policy table is the single grep-able
answer to "who can ban?".

Legacy error messages are preserved verbatim via the
``_MODERATOR_REQUIRED`` / ``_ADMIN_REQUIRED`` constants — the UI's
403 banner and several tests match on the exact strings.
"""
from __future__ import annotations

from datetime import datetime

from src.domain.moderation import Flag, Sanction
from src.repositories.moderation_repository import ModerationRepository
from src.repositories.user_repository import UserRepository
from src.services.authz import Action, AuthorizationService
from src.services.exceptions import Conflict, PermissionDenied

AUTO_HIDE_THRESHOLD = 3

# Legacy 403 messages preserved across the migration so the UI's denial
# banner + the existing test contracts keep working. Single constants
# because Sonar S1192 flags duplicated literals (and they have to match
# byte-for-byte across every call site).
_MODERATOR_REQUIRED = "Moderator role required"
_ADMIN_REQUIRED = "Admin role required"


class ModerationService:
    def __init__(
        self,
        mod: ModerationRepository,
        users: UserRepository,
        authz: AuthorizationService,
    ) -> None:
        self._mod = mod
        self._users = users
        self._authz = authz

    async def _require(self, user_id: str, action: Action, legacy_message: str) -> None:
        """Run the policy and surface the legacy 403 message on deny.

        The audit record uses the policy's verdict reason; the
        exception kept here is purely about preserving the contract
        the UI + existing tests rely on.
        """
        user = await self._users.get_by_id(user_id)
        if user is None:
            raise PermissionDenied("User not found")
        principal = await self._authz.principal(user_id)
        try:
            await self._authz.require(principal, action)
        except PermissionDenied as e:
            raise PermissionDenied(legacy_message) from e

    # pylint: disable-next=too-many-arguments,too-many-positional-arguments
    async def flag(
        self,
        user_id: str,
        target_type: str,
        target_id: str,
        reason: str,
        details: str = "",
    ) -> Flag:
        principal = await self._authz.principal(user_id)
        await self._authz.require(principal, Action.FLAGS_CREATE)
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
            # Auto-hide hook intentionally left dormant — wired up in
            # the moderation v2 PR once the UI has a "hidden by
            # community" surface to render.
            pass
        return flag

    # ``type`` mirrors the Sanction.type field and the public REST
    # body shape (CreateSanctionRequest.type). Renaming it just here
    # would force every caller into a translation step and trip the
    # OpenAPI schema diff. The shadow is local-only and obvious.
    # pylint: disable-next=too-many-arguments,too-many-positional-arguments,redefined-builtin
    async def sanction(self, moderator_id: str, user_id: str, type: str,
                       reason: str, expires_at: datetime | None = None) -> Sanction:
        if type == "ban":
            await self._require(moderator_id, Action.SANCTIONS_BAN, _ADMIN_REQUIRED)
        else:
            await self._require(moderator_id, Action.SANCTIONS_CREATE, _MODERATOR_REQUIRED)
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
        await self._require(moderator_id, Action.SANCTIONS_REVOKE, _MODERATOR_REQUIRED)
        await self._mod.lift_sanction(sanction_id)
        await self._users.lift_sanction(sanction_id)

    async def get_queue(self, moderator_id: str, limit: int, offset: int) -> list[Flag]:
        await self._require(moderator_id, Action.FLAGS_READ_QUEUE, _MODERATOR_REQUIRED)
        return await self._mod.list_flagged(limit, offset)

    async def resolve_flags(
        self, moderator_id: str, target_type: str, target_id: str, action: str
    ) -> None:
        await self._require(moderator_id, Action.FLAGS_RESOLVE, _MODERATOR_REQUIRED)
        await self._mod.resolve_flags(target_type, target_id, action, moderator_id)

    async def get_log(self, moderator_id: str, limit: int, offset: int) -> list[dict]:
        await self._require(moderator_id, Action.MODERATION_READ_LOG, _MODERATOR_REQUIRED)
        return await self._mod.get_log(limit, offset)
