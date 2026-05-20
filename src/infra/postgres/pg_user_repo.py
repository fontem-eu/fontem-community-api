from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.moderation import Sanction
from src.services.exceptions import Conflict
from src.domain.user import User
from src.infra.postgres.models import SanctionModel, UserModel, UserRoleModel
from src.repositories.user_repository import UserRepository


class PgUserRepository(UserRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _to_domain(row: UserModel) -> User:
        return User(
            id=row.id,
            email=row.email,
            name=row.name,
            avatar_url=row.avatar_url,
            password_hash=getattr(row, 'password_hash', None),
            trust_level=row.trust_level,
            created_at=row.created_at,
            failed_login_attempts=getattr(row, 'failed_login_attempts', 0) or 0,
            locked_until=getattr(row, 'locked_until', None),
        )

    async def get_by_id(self, user_id: str) -> User | None:
        result = await self._session.execute(
            select(UserModel).where(UserModel.id == user_id)
        )
        row = result.scalar_one_or_none()
        return self._to_domain(row) if row else None

    async def get_by_email(self, email: str) -> User | None:
        result = await self._session.execute(
            select(UserModel).where(UserModel.email == email)
        )
        row = result.scalar_one_or_none()
        return self._to_domain(row) if row else None

    async def upsert(self, user: User) -> User:
        user_id = user.id or str(uuid4())
        now = datetime.now(timezone.utc)
        stmt = pg_insert(UserModel).values(
            id=user_id,
            email=user.email,
            name=user.name,
            avatar_url=user.avatar_url,
            password_hash=user.password_hash,
            trust_level=user.trust_level,
            created_at=user.created_at or now,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["id"],
            set_={
                "email": stmt.excluded.email,
                "name": stmt.excluded.name,
                "avatar_url": stmt.excluded.avatar_url,
                "trust_level": stmt.excluded.trust_level,
            },
        )
        try:
            await self._session.execute(stmt)
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            if "email" in str(exc).lower():
                raise Conflict(f"Email {user.email} already registered") from exc
            raise
        return await self.get_by_id(user_id)  # type: ignore[return-value]

    async def get_roles(self, user_id: str) -> list[str]:
        result = await self._session.execute(
            select(UserRoleModel.role).where(UserRoleModel.user_id == user_id)
        )
        return list(result.scalars().all())

    async def set_roles(self, user_id: str, roles: list[str]) -> None:
        await self._session.execute(
            delete(UserRoleModel).where(UserRoleModel.user_id == user_id)
        )
        for role in roles:
            self._session.add(UserRoleModel(user_id=user_id, role=role))
        await self._session.commit()

    async def set_trust_level(self, user_id: str, level: str) -> None:
        result = await self._session.execute(
            select(UserModel).where(UserModel.id == user_id)
        )
        row = result.scalar_one_or_none()
        if row is not None:
            row.trust_level = level
            await self._session.commit()

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
        if row is None:
            return None
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

    async def add_sanction(self, sanction: Sanction) -> None:
        row = SanctionModel(
            id=sanction.id or str(__import__("uuid").uuid4()),
            user_id=sanction.user_id,
            type=sanction.type,
            reason=sanction.reason,
            starts_at=sanction.starts_at,
            expires_at=sanction.expires_at,
            applied_by=sanction.applied_by,
        )
        self._session.add(row)
        await self._session.commit()

    async def lift_sanction(self, sanction_id: str) -> None:
        result = await self._session.execute(
            select(SanctionModel).where(SanctionModel.id == sanction_id)
        )
        row = result.scalar_one_or_none()
        if row is not None:
            row.lifted_at = datetime.now(timezone.utc)
            await self._session.commit()

    async def register_failed_login(
        self, email: str, max_attempts: int, lock_duration_minutes: int,
    ) -> None:
        result = await self._session.execute(
            select(UserModel).where(UserModel.email == email)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return  # silently ignore unknown emails (no account-existence leak)
        new_count = (getattr(row, 'failed_login_attempts', 0) or 0) + 1
        values: dict = {"failed_login_attempts": new_count}
        if new_count >= max_attempts:
            values["locked_until"] = (
                datetime.now(timezone.utc) + timedelta(minutes=lock_duration_minutes)
            )
        await self._session.execute(
            update(UserModel).where(UserModel.id == row.id).values(**values)
        )
        await self._session.commit()

    async def clear_failed_logins(self, user_id: str) -> None:
        await self._session.execute(
            update(UserModel)
            .where(UserModel.id == user_id)
            .values(failed_login_attempts=0, locked_until=None)
        )
        await self._session.commit()
