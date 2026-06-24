"""Shared "who has access & why" resolver for dossiers + viz (Phase D)."""
from __future__ import annotations

from src.domain.investigation_roles import ROLE_TO_LEVEL
from src.services.permission_service import LEVEL_HIERARCHY


async def _effective_access(inv_repo, grant_repo, users_repo, resource_type, resource_id, created_by, investigation_id):
    rows: dict[str, dict] = {}

    def add(uid, level, source):
        if uid is None:
            return
        cur = rows.get(uid)
        if cur is None or LEVEL_HIERARCHY.get(level, 0) > LEVEL_HIERARCHY.get(cur["level"], 0):
            rows[uid] = {"level": level, "source": source}

    add(created_by, "owner", "owner")
    if investigation_id:
        for m in await inv_repo.list_members(investigation_id):
            add(m.user_id, ROLE_TO_LEVEL.get(m.role, "viewer"), f"inherited:{m.role}")
    for g in await grant_repo.list_grants(resource_type, resource_id):
        add(g.user_id, g.level, "direct")

    out = []
    for uid, info in rows.items():
        u = await users_repo.get_by_id(uid)
        out.append({
            "user_id": uid, "email": u.email if u else None,
            "name": u.name if u else None, **info,
        })
    return out
