"""PostgreSQL repository for the feed-query catalogue."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.named_query import (
    ContractCheck,
    ContractReport,
    NamedQuery,
    QueryGroup,
    QueryParam,
)
from src.infra.postgres.models import (
    NamedQueryModel,
    QueryGroupMemberModel,
    QueryGroupModel,
)
from src.repositories.named_query_repository import NamedQueryRepository


def _report_to_domain(raw) -> ContractReport | None:
    if not raw:
        return None
    checked = raw.get("checked_at")
    return ContractReport(
        subscribable=bool(raw.get("subscribable")),
        checks=[ContractCheck(**c) for c in (raw.get("checks") or [])],
        columns=list(raw.get("columns") or []),
        row_count=int(raw.get("row_count") or 0),
        duration_ms=int(raw.get("duration_ms") or 0),
        error=raw.get("error"),
        checked_at=datetime.fromisoformat(checked) if isinstance(checked, str) else checked,
    )


def _report_to_json(report: ContractReport | None) -> dict | None:
    if report is None:
        return None
    out = asdict(report)
    if isinstance(out.get("checked_at"), datetime):
        out["checked_at"] = out["checked_at"].isoformat()
    return out


class PgNamedQueryRepository(NamedQueryRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _query_to_domain(row: NamedQueryModel) -> NamedQuery:
        return NamedQuery(
            id=row.id, slug=row.slug, name=row.name, description=row.description,
            lang=row.lang, query=row.query,
            params=[QueryParam(**p) for p in (row.params or [])],
            status=row.status, waivers=dict(row.waivers or {}),
            contract_ok=row.contract_ok,
            contract_report=_report_to_domain(row.contract_report),
            validated_at=row.validated_at, created_by=row.created_by,
            created_at=row.created_at, updated_at=row.updated_at,
        )

    @staticmethod
    def _group_to_domain(row: QueryGroupModel, queries: list[NamedQuery] | None = None) -> QueryGroup:
        return QueryGroup(
            id=row.id, slug=row.slug, name=row.name, description=row.description,
            sort_order=row.sort_order, visibility=row.visibility,
            created_at=row.created_at, updated_at=row.updated_at,
            queries=queries or [],
        )

    # ── named queries ────────────────────────────────────────
    async def create_query(self, query: NamedQuery) -> NamedQuery:
        now = datetime.now(timezone.utc)
        model = NamedQueryModel(
            id=query.id or str(uuid4()), slug=query.slug, name=query.name,
            description=query.description, lang=query.lang, query=query.query,
            params=[asdict(p) for p in query.params], status=query.status,
            waivers=dict(query.waivers or {}), contract_ok=query.contract_ok,
            contract_report=_report_to_json(query.contract_report),
            validated_at=query.validated_at, created_by=query.created_by,
            created_at=now, updated_at=now,
        )
        self._session.add(model)
        await self._session.commit()
        return self._query_to_domain(model)

    async def get_query(self, query_id: str) -> NamedQuery | None:
        row = await self._session.get(NamedQueryModel, query_id)
        return self._query_to_domain(row) if row else None

    async def get_query_by_slug(self, slug: str) -> NamedQuery | None:
        stmt = select(NamedQueryModel).where(NamedQueryModel.slug == slug)
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return self._query_to_domain(row) if row else None

    async def list_queries(self, status: str | None = None) -> list[NamedQuery]:
        stmt = select(NamedQueryModel).order_by(NamedQueryModel.name)
        if status:
            stmt = stmt.where(NamedQueryModel.status == status)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [self._query_to_domain(r) for r in rows]

    async def update_query(self, query: NamedQuery) -> NamedQuery:
        model = await self._session.get(NamedQueryModel, query.id)
        if model is None:
            return query
        model.slug = query.slug
        model.name = query.name
        model.description = query.description
        model.lang = query.lang
        model.query = query.query
        model.params = [asdict(p) for p in query.params]
        model.status = query.status
        model.waivers = dict(query.waivers or {})
        model.contract_ok = query.contract_ok
        model.contract_report = _report_to_json(query.contract_report)
        model.validated_at = query.validated_at
        model.updated_at = datetime.now(timezone.utc)
        await self._session.commit()
        return self._query_to_domain(model)

    async def delete_query(self, query_id: str) -> None:
        model = await self._session.get(NamedQueryModel, query_id)
        if model is not None:
            await self._session.delete(model)
            await self._session.commit()

    # ── groups ───────────────────────────────────────────────
    async def create_group(self, group: QueryGroup) -> QueryGroup:
        now = datetime.now(timezone.utc)
        model = QueryGroupModel(
            id=group.id or str(uuid4()), slug=group.slug, name=group.name,
            description=group.description, sort_order=group.sort_order,
            visibility=group.visibility, created_at=now, updated_at=now,
        )
        self._session.add(model)
        await self._session.commit()
        return self._group_to_domain(model)

    async def _load_members(self, group_id: str) -> list[NamedQuery]:
        stmt = (
            select(NamedQueryModel)
            .join(QueryGroupMemberModel,
                  QueryGroupMemberModel.query_id == NamedQueryModel.id)
            .where(QueryGroupMemberModel.group_id == group_id)
            .order_by(QueryGroupMemberModel.sort_order)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [self._query_to_domain(r) for r in rows]

    async def get_group(self, group_id: str) -> QueryGroup | None:
        row = await self._session.get(QueryGroupModel, group_id)
        if row is None:
            return None
        return self._group_to_domain(row, await self._load_members(row.id))

    async def get_group_by_slug(self, slug: str) -> QueryGroup | None:
        stmt = select(QueryGroupModel).where(QueryGroupModel.slug == slug)
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        return self._group_to_domain(row, await self._load_members(row.id))

    async def list_groups(self, visibility: str | None = None) -> list[QueryGroup]:
        stmt = select(QueryGroupModel).order_by(
            QueryGroupModel.sort_order, QueryGroupModel.name,
        )
        if visibility:
            stmt = stmt.where(QueryGroupModel.visibility == visibility)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [self._group_to_domain(r, await self._load_members(r.id)) for r in rows]

    async def update_group(self, group: QueryGroup) -> QueryGroup:
        model = await self._session.get(QueryGroupModel, group.id)
        if model is None:
            return group
        model.slug = group.slug
        model.name = group.name
        model.description = group.description
        model.sort_order = group.sort_order
        model.visibility = group.visibility
        model.updated_at = datetime.now(timezone.utc)
        await self._session.commit()
        return self._group_to_domain(model, await self._load_members(model.id))

    async def delete_group(self, group_id: str) -> None:
        model = await self._session.get(QueryGroupModel, group_id)
        if model is not None:
            await self._session.delete(model)
            await self._session.commit()

    # ── membership ───────────────────────────────────────────
    async def set_group_queries(self, group_id: str, query_ids: list[str]) -> None:
        await self._session.execute(
            delete(QueryGroupMemberModel).where(QueryGroupMemberModel.group_id == group_id)
        )
        for position, query_id in enumerate(query_ids):
            self._session.add(QueryGroupMemberModel(
                group_id=group_id, query_id=query_id, sort_order=position,
            ))
        await self._session.commit()

    async def groups_for_query(self, query_id: str) -> list[QueryGroup]:
        stmt = (
            select(QueryGroupModel)
            .join(QueryGroupMemberModel,
                  QueryGroupMemberModel.group_id == QueryGroupModel.id)
            .where(QueryGroupMemberModel.query_id == query_id)
            .order_by(QueryGroupModel.sort_order, QueryGroupModel.name)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [self._group_to_domain(r) for r in rows]
