# ``sqlalchemy.func.count`` is a magic factory pylint can't introspect;
# every call lights up E1102 as a false positive. See pg_report_repo.py.
# pylint: disable=not-callable
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.moderation import Flag, Sanction
from src.infra.postgres.models import FlagModel, ModerationLogModel, SanctionModel
from src.repositories.moderation_repository import ModerationRepository


class PgModerationRepository(ModerationRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── domain ↔ ORM helpers ──────────────────────────────────────

    @staticmethod
    def _flag_to_domain(row: FlagModel) -> Flag:
        return Flag(
            id=row.id,
            target_type=row.target_type,
            target_id=row.target_id,
            reason=row.reason,
            details=row.details,
            flagged_by=row.flagged_by,
            created_at=row.created_at,
        )

    @staticmethod
    def _sanction_to_domain(row: SanctionModel) -> Sanction:
        return Sanction(
            id=row.id,
            user_id=row.user_id,
            type=row.type,
            reason=row.reason,
            starts_at=row.starts_at,
            expires_at=row.expires_at,
            applied_by=row.applied_by,
            lifted_at=row.lifted_at,
        )

    @staticmethod
    def _log_to_dict(row: ModerationLogModel) -> dict:
        return {
            "id": row.id,
            "action": row.action,
            "details": row.details,
            "actor_id": row.actor_id,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }

    async def _append_log(self, action: str, details: dict, actor_id: str) -> None:
        self._session.add(
            ModerationLogModel(
                action=action,
                details=details,
                actor_id=actor_id,
                created_at=datetime.now(timezone.utc),
            )
        )

    # ── Flags ─────────────────────────────────────────────────────

    async def add_flag(self, flag: Flag) -> Flag:
        now = datetime.now(timezone.utc)
        model = FlagModel(
            id=flag.id or str(uuid4()),
            target_type=flag.target_type,
            target_id=flag.target_id,
            reason=flag.reason,
            details=flag.details,
            flagged_by=flag.flagged_by,
            created_at=flag.created_at or now,
        )
        self._session.add(model)
        await self._append_log(
            action="flag_added",
            details={
                "flag_id": model.id,
                "target_type": flag.target_type,
                "target_id": flag.target_id,
                "flagged_by": flag.flagged_by,
            },
            actor_id=flag.flagged_by,
        )
        await self._session.commit()
        return self._flag_to_domain(model)

    async def count_flags(self, target_type: str, target_id: str) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(FlagModel)
            .where(FlagModel.target_type == target_type)
            .where(FlagModel.target_id == target_id)
        )
        return result.scalar_one()

    async def has_flagged(self, target_type: str, target_id: str, user_id: str) -> bool:
        result = await self._session.execute(
            select(func.count())
            .select_from(FlagModel)
            .where(FlagModel.target_type == target_type)
            .where(FlagModel.target_id == target_id)
            .where(FlagModel.flagged_by == user_id)
        )
        return result.scalar_one() > 0

    async def list_flagged(self, limit: int, offset: int) -> list[Flag]:
        result = await self._session.execute(
            select(FlagModel)
            .order_by(FlagModel.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return [self._flag_to_domain(r) for r in result.scalars().all()]

    async def resolve_flags(
        self, target_type: str, target_id: str, action: str, moderator_id: str
    ) -> None:
        await self._session.execute(
            delete(FlagModel)
            .where(FlagModel.target_type == target_type)
            .where(FlagModel.target_id == target_id)
        )
        await self._append_log(
            action="flags_resolved",
            details={
                "target_type": target_type,
                "target_id": target_id,
                "resolution": action,
            },
            actor_id=moderator_id,
        )
        await self._session.commit()

    # ── Sanctions ─────────────────────────────────────────────────

    async def add_sanction(self, sanction: Sanction) -> Sanction:
        now = datetime.now(timezone.utc)
        model = SanctionModel(
            id=sanction.id or str(uuid4()),
            user_id=sanction.user_id,
            type=sanction.type,
            reason=sanction.reason,
            starts_at=sanction.starts_at or now,
            expires_at=sanction.expires_at,
            applied_by=sanction.applied_by,
            lifted_at=sanction.lifted_at,
        )
        self._session.add(model)
        await self._append_log(
            action="sanction_applied",
            details={
                "sanction_id": model.id,
                "user_id": sanction.user_id,
                "type": sanction.type,
            },
            actor_id=sanction.applied_by,
        )
        await self._session.commit()
        return self._sanction_to_domain(model)

    async def get_active_sanction(self, user_id: str) -> Sanction | None:
        now = datetime.now(timezone.utc)
        result = await self._session.execute(
            select(SanctionModel)
            .where(SanctionModel.user_id == user_id)
            .where(SanctionModel.lifted_at.is_(None))
            .where(
                (SanctionModel.expires_at.is_(None)) | (SanctionModel.expires_at > now)
            )
            .limit(1)
        )
        row = result.scalar_one_or_none()
        return self._sanction_to_domain(row) if row else None

    async def lift_sanction(self, sanction_id: str) -> None:
        now = datetime.now(timezone.utc)
        result = await self._session.execute(
            select(SanctionModel).where(SanctionModel.id == sanction_id)
        )
        row = result.scalar_one_or_none()
        if row is not None:
            row.lifted_at = now
            await self._append_log(
                action="sanction_lifted",
                details={"sanction_id": sanction_id},
                actor_id=row.applied_by,
            )
            await self._session.commit()

    async def get_log(self, limit: int, offset: int) -> list[dict]:
        result = await self._session.execute(
            select(ModerationLogModel)
            .order_by(ModerationLogModel.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return [self._log_to_dict(r) for r in result.scalars().all()]
