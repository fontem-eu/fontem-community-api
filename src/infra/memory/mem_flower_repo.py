"""In-memory FlowerRepository used by the unit-test conftest."""
from __future__ import annotations

from src.repositories.flower_repository import FlowerRepository


class InMemoryFlowerRepository(FlowerRepository):
    def __init__(self) -> None:
        self._counts: dict[tuple[str, str], int] = {}

    async def get_mine(self, user_id: str, report_id: str) -> int:
        return self._counts.get((user_id, report_id), 0)

    async def get_total(self, report_id: str) -> int:
        return sum(
            n for (_u, r), n in self._counts.items() if r == report_id
        )

    async def increment(
        self, user_id: str, report_id: str, *, cap: int,
    ) -> int | None:
        key = (user_id, report_id)
        current = self._counts.get(key, 0)
        if current >= cap:
            return None
        self._counts[key] = current + 1
        return self._counts[key]
