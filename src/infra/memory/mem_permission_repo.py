from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

from src.domain.report import AccessGrant
from src.repositories.group_repository import GroupRepository
from src.repositories.permission_repository import PermissionRepository

LEVEL_HIERARCHY: dict[str, int] = {
    "viewer": 0,
    "commenter": 1,
    "editor": 2,
    "owner": 3,
}


class InMemoryPermissionRepository(PermissionRepository):
    def __init__(self, group_repo: GroupRepository, report_repo=None) -> None:
        self._grants: list[AccessGrant] = []
        self._visibility: dict[str, str] = {}
        self._group_repo = group_repo
        self._report_repo = report_repo  # optional, for visibility lookup

    async def get_report_access(self, user_id: str, report_id: str) -> str | None:
        best_level: str | None = None
        best_rank = -1

        # Direct user access
        for g in self._grants:
            if g.report_id == report_id and g.user_id == user_id:
                rank = LEVEL_HIERARCHY.get(g.level, -1)
                if rank > best_rank:
                    best_rank = rank
                    best_level = g.level

        # Group access
        user_groups = await self._group_repo.get_user_groups(user_id)
        group_ids = {grp.id for grp in user_groups}
        for g in self._grants:
            if g.report_id == report_id and g.group_id in group_ids:
                rank = LEVEL_HIERARCHY.get(g.level, -1)
                if rank > best_rank:
                    best_rank = rank
                    best_level = g.level

        return best_level

    async def get_report_visibility(self, report_id: str) -> str | None:
        # Check explicit visibility first, then fall back to report's own visibility
        v = self._visibility.get(report_id)
        if v:
            return v
        if self._report_repo:
            report = await self._report_repo.get_by_id(report_id)
            if report:
                return report.visibility
        return None

    async def set_user_access(self, report_id: str, user_id: str, level: str) -> None:
        # Remove existing grant for this user on this report
        self._grants = [
            g
            for g in self._grants
            if not (g.report_id == report_id and g.user_id == user_id)
        ]
        self._grants.append(
            AccessGrant(
                id=str(uuid4()),
                report_id=report_id,
                user_id=user_id,
                level=level,
            )
        )

    async def set_group_access(self, report_id: str, group_id: str, level: str) -> None:
        self._grants = [
            g
            for g in self._grants
            if not (g.report_id == report_id and g.group_id == group_id)
        ]
        self._grants.append(
            AccessGrant(
                id=str(uuid4()),
                report_id=report_id,
                group_id=group_id,
                level=level,
            )
        )

    async def remove_user_access(self, report_id: str, user_id: str) -> None:
        self._grants = [
            g
            for g in self._grants
            if not (g.report_id == report_id and g.user_id == user_id)
        ]

    async def remove_group_access(self, report_id: str, group_id: str) -> None:
        self._grants = [
            g
            for g in self._grants
            if not (g.report_id == report_id and g.group_id == group_id)
        ]

    async def list_collaborators(self, report_id: str) -> list[AccessGrant]:
        return [deepcopy(g) for g in self._grants if g.report_id == report_id]

    def set_visibility(self, report_id: str, visibility: str) -> None:
        """Internal helper to sync visibility from report updates."""
        self._visibility[report_id] = visibility
