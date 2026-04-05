from __future__ import annotations

from abc import ABC, abstractmethod

from src.domain.moderation import Sanction
from src.domain.user import User


class UserRepository(ABC):
    @abstractmethod
    async def get_by_id(self, user_id: str) -> User | None: ...

    @abstractmethod
    async def get_by_email(self, email: str) -> User | None: ...

    @abstractmethod
    async def upsert(self, user: User) -> User: ...

    @abstractmethod
    async def get_roles(self, user_id: str) -> list[str]: ...

    @abstractmethod
    async def set_roles(self, user_id: str, roles: list[str]) -> None: ...

    @abstractmethod
    async def get_active_sanction(self, user_id: str) -> Sanction | None: ...

    @abstractmethod
    async def add_sanction(self, sanction: Sanction) -> None: ...

    @abstractmethod
    async def lift_sanction(self, sanction_id: str) -> None: ...
