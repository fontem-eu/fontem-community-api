from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4

from src.domain.moderation import Flag, Sanction
from src.repositories.moderation_repository import ModerationRepository


class InMemoryModerationRepository(ModerationRepository):
    def __init__(self) -> None:
        self._flags: list[Flag] = []
        self._sanctions: list[Sanction] = []
        self._log: list[dict] = []

    async def add_flag(self, flag: Flag) -> Flag:
        if flag.id is None:
            flag.id = str(uuid4())
        flag.created_at = flag.created_at or datetime.now(timezone.utc)
        self._flags.append(deepcopy(flag))
        self._log.append(
            {
                "action": "flag_added",
                "flag_id": flag.id,
                "target_type": flag.target_type,
                "target_id": flag.target_id,
                "flagged_by": flag.flagged_by,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        return deepcopy(flag)

    async def count_flags(self, target_type: str, target_id: str) -> int:
        return sum(
            1
            for f in self._flags
            if f.target_type == target_type and f.target_id == target_id
        )

    async def has_flagged(self, target_type: str, target_id: str, user_id: str) -> bool:
        return any(
            f.target_type == target_type
            and f.target_id == target_id
            and f.flagged_by == user_id
            for f in self._flags
        )

    async def list_flagged(self, limit: int, offset: int) -> list[Flag]:
        sorted_flags = sorted(
            self._flags, key=lambda f: f.created_at or datetime.min, reverse=True
        )
        return [deepcopy(f) for f in sorted_flags[offset : offset + limit]]

    async def resolve_flags(
        self, target_type: str, target_id: str, action: str, moderator_id: str
    ) -> None:
        self._flags = [
            f
            for f in self._flags
            if not (f.target_type == target_type and f.target_id == target_id)
        ]
        self._log.append(
            {
                "action": "flags_resolved",
                "target_type": target_type,
                "target_id": target_id,
                "resolution": action,
                "moderator_id": moderator_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

    async def add_sanction(self, sanction: Sanction) -> Sanction:
        if sanction.id is None:
            sanction.id = str(uuid4())
        sanction.starts_at = sanction.starts_at or datetime.now(timezone.utc)
        self._sanctions.append(deepcopy(sanction))
        self._log.append(
            {
                "action": "sanction_applied",
                "sanction_id": sanction.id,
                "user_id": sanction.user_id,
                "type": sanction.type,
                "applied_by": sanction.applied_by,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        return deepcopy(sanction)

    async def get_active_sanction(self, user_id: str) -> Sanction | None:
        now = datetime.now(timezone.utc)
        for sanction in reversed(self._sanctions):
            if sanction.user_id != user_id:
                continue
            if sanction.lifted_at is not None:
                continue
            if sanction.expires_at is not None and sanction.expires_at < now:
                continue
            return deepcopy(sanction)
        return None

    async def lift_sanction(self, sanction_id: str) -> None:
        now = datetime.now(timezone.utc)
        for sanction in self._sanctions:
            if sanction.id == sanction_id:
                sanction.lifted_at = now
                self._log.append(
                    {
                        "action": "sanction_lifted",
                        "sanction_id": sanction_id,
                        "timestamp": now.isoformat(),
                    }
                )
                break

    async def get_log(self, limit: int, offset: int) -> list[dict]:
        sorted_log = list(reversed(self._log))
        return sorted_log[offset : offset + limit]
