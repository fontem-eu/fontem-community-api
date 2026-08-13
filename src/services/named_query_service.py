"""The feed-query catalogue service.

Admins author named queries here, run them, and get a recorded verdict on
whether each one satisfies the feed contract. Groups are ordered sets of those
queries, and are what the public picker shows.

Two rules carry most of the weight:

**Publishing is gated on the contract.** A query cannot go from draft to
published unless its last validation says subscribable. Otherwise the
catalogue fills up with queries that look available and re-notify every
subscriber on every run.

**Editing the body invalidates the verdict.** A stored "yes" that refers to a
previous version of the query is worse than no verdict at all, so any change
to the body, engine or waivers clears contract_ok and drops the query back to
draft. Renaming or re-describing does not — that is not the thing that was
validated.
"""
from __future__ import annotations

import re
from copy import deepcopy
from datetime import datetime, timezone

from src.domain.named_query import (
    ContractReport,
    LANGS,
    NamedQuery,
    QueryGroup,
    QueryParam,
    STATUSES,
)
from src.repositories.named_query_repository import NamedQueryRepository
from src.services import feed_contract
from src.services.authz import Action, AuthorizationService
from src.services.exceptions import Conflict, InvalidInput, NotFound
from src.services.query_executor import QueryExecutor

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# A preview runs an arbitrary admin-authored query. The proxy caps the damage
# (read-only, row-capped, statement-timed), but we cap the payload we hand
# back so a wide result set doesn't have to be rendered in a browser panel.
PREVIEW_ROW_LIMIT = 50


class NamedQueryService:  # pylint: disable=too-many-public-methods
    def __init__(
        self,
        repo: NamedQueryRepository,
        authz: AuthorizationService,
        executor: QueryExecutor,
    ) -> None:
        self._repo = repo
        self._authz = authz
        self._executor = executor

    # ── guards ───────────────────────────────────────────────
    async def _require(self, user_id: str, action: Action) -> None:
        principal = await self._authz.principal(user_id)
        await self._authz.require(principal, action)

    @staticmethod
    def _validate_slug(slug: str) -> str:
        slug = (slug or "").strip().lower()
        if not SLUG_RE.match(slug):
            raise InvalidInput(
                "Slug must be lowercase words separated by single hyphens "
                "(e.g. 'public-contracts-by-region')"
            )
        return slug

    @staticmethod
    def _validate_lang(lang: str) -> str:
        if lang not in LANGS:
            raise InvalidInput(f"Engine must be one of {', '.join(LANGS)}")
        return lang

    @staticmethod
    def _to_params(raw: list | None) -> list[QueryParam]:
        out: list[QueryParam] = []
        for item in raw or []:
            if not isinstance(item, dict) or not str(item.get("name") or "").strip():
                raise InvalidInput("Every declared parameter needs a name")
            out.append(QueryParam(
                name=str(item["name"]).strip(),
                type=str(item.get("type") or "text"),
                label=str(item.get("label") or ""),
                required=bool(item.get("required")),
                default=item.get("default"),
            ))
        return out

    # ── named queries ────────────────────────────────────────
    async def list_queries(self, user_id: str, status: str | None = None) -> list[NamedQuery]:
        await self._require(user_id, Action.FEEDS_MANAGE_QUERIES)
        return await self._repo.list_queries(status)

    async def get_query(self, user_id: str, query_id: str) -> NamedQuery:
        await self._require(user_id, Action.FEEDS_MANAGE_QUERIES)
        found = await self._repo.get_query(query_id)
        if found is None:
            raise NotFound(f"Named query {query_id} not found")
        return found

    async def create_query(self, user_id: str, **fields) -> NamedQuery:
        await self._require(user_id, Action.FEEDS_MANAGE_QUERIES)
        slug = self._validate_slug(fields.get("slug") or "")
        if await self._repo.get_query_by_slug(slug) is not None:
            raise Conflict(f"A named query with the slug '{slug}' already exists")
        query = NamedQuery(
            slug=slug,
            name=(fields.get("name") or "").strip() or slug,
            description=fields.get("description") or "",
            lang=self._validate_lang(fields.get("lang") or "sql"),
            query=fields.get("query") or "",
            params=self._to_params(fields.get("params")),
            waivers=dict(fields.get("waivers") or {}),
            status="draft",
            created_by=user_id,
        )
        return await self._repo.create_query(query)

    async def update_query(self, user_id: str, query_id: str, **fields) -> NamedQuery:
        await self._require(user_id, Action.FEEDS_MANAGE_QUERIES)
        current = await self._repo.get_query(query_id)
        if current is None:
            raise NotFound(f"Named query {query_id} not found")

        updated = deepcopy(current)
        if fields.get("slug") is not None:
            slug = self._validate_slug(fields["slug"])
            clash = await self._repo.get_query_by_slug(slug)
            if clash is not None and clash.id != query_id:
                raise Conflict(f"A named query with the slug '{slug}' already exists")
            updated.slug = slug
        for key in ("name", "description"):
            if fields.get(key) is not None:
                setattr(updated, key, fields[key])

        if self._apply_body_edits(updated, current, fields):
            self._invalidate(updated)

        if fields.get("status") is not None:
            updated.status = self._check_status(fields["status"], updated)

        return await self._repo.update_query(updated)

    def _apply_body_edits(self, updated: NamedQuery, current: NamedQuery, fields: dict) -> bool:
        """Apply the fields that change what would actually run, and report
        whether any of them moved. Those are the edits that invalidate a
        stored verdict — see the module docstring."""
        substantive = False
        if fields.get("lang") is not None and fields["lang"] != current.lang:
            updated.lang = self._validate_lang(fields["lang"])
            substantive = True
        if fields.get("query") is not None and fields["query"] != current.query:
            updated.query = fields["query"]
            substantive = True
        if fields.get("params") is not None:
            updated.params = self._to_params(fields["params"])
            substantive = True
        if fields.get("waivers") is not None:
            new_waivers = dict(fields["waivers"] or {})
            if new_waivers != current.waivers:
                updated.waivers = new_waivers
                substantive = True
        return substantive

    @staticmethod
    def _invalidate(query: NamedQuery) -> None:
        query.contract_ok = False
        query.contract_report = None
        query.validated_at = None
        if query.status == "published":
            query.status = "draft"

    @staticmethod
    def _check_status(status: str, query: NamedQuery) -> str:
        if status not in STATUSES:
            raise InvalidInput(f"Status must be one of {', '.join(STATUSES)}")
        if status == "published" and not query.contract_ok:
            raise InvalidInput(
                "This query cannot be published until it passes the feed contract. "
                "Run validation and address the failing checks first."
            )
        return status

    async def delete_query(self, user_id: str, query_id: str) -> None:
        await self._require(user_id, Action.FEEDS_MANAGE_QUERIES)
        await self._repo.delete_query(query_id)

    # ── validation + preview ─────────────────────────────────
    async def validate_query(self, user_id: str, query_id: str) -> NamedQuery:
        """Run the full contract check and persist the verdict."""
        await self._require(user_id, Action.FEEDS_MANAGE_QUERIES)
        query = await self._repo.get_query(query_id)
        if query is None:
            raise NotFound(f"Named query {query_id} not found")

        report = await self._build_report(query)
        query.contract_report = report
        query.contract_ok = report.subscribable
        query.validated_at = report.checked_at
        if not report.subscribable and query.status == "published":
            # A published query that no longer validates (the store changed
            # under it) must stop being offered.
            query.status = "draft"
        return await self._repo.update_query(query)

    async def _build_report(self, query: NamedQuery) -> ContractReport:
        checks = feed_contract.static_checks(query)
        blocking = [c for c in checks if not c.passed and c.id in
                    ("lang", "not_empty", "size", "read_only")]

        if blocking:
            # Don't execute something we already know the proxy will reject;
            # the static failure is the useful message.
            checks = feed_contract.apply_waivers(checks, query.waivers)
            return ContractReport(
                subscribable=False, checks=checks,
                checked_at=datetime.now(timezone.utc),
                error="not executed — the query fails a static check",
            )

        params = feed_contract.sample_params()
        first = await self._executor.run(query.lang, query.query, params)
        second = None
        if not first.error:
            second = await self._executor.run(query.lang, query.query, params)
        checks = checks + feed_contract.runtime_checks(first, second)
        checks = feed_contract.apply_waivers(checks, query.waivers)

        return ContractReport(
            subscribable=feed_contract.is_subscribable(checks),
            checks=checks,
            columns=list(first.columns or []),
            row_count=first.row_count,
            duration_ms=first.duration_ms,
            error=first.error,
            checked_at=datetime.now(timezone.utc),
        )

    async def preview(
        self, user_id: str, draft: NamedQuery, params: dict | None = None,
    ) -> dict:
        """Run an unsaved draft and report both its rows and its verdict.

        Takes a whole (unsaved) NamedQuery rather than an id so the editor can
        check work in progress without first saving something broken into the
        catalogue.
        """
        await self._require(user_id, Action.FEEDS_MANAGE_QUERIES)
        self._validate_lang(draft.lang)
        report = await self._build_report(draft)

        merged = {**feed_contract.sample_params(), **(params or {})}
        result = await self._executor.run(draft.lang, draft.query, merged)
        return {
            "columns": result.columns,
            "rows": result.rows[:PREVIEW_ROW_LIMIT],
            "row_count": result.row_count,
            "truncated": result.truncated or result.row_count > PREVIEW_ROW_LIMIT,
            "duration_ms": result.duration_ms,
            "error": result.error,
            "params_used": merged,
            "contract": report,
        }

    # ── groups ───────────────────────────────────────────────
    async def list_groups(self, user_id: str) -> list[QueryGroup]:
        await self._require(user_id, Action.FEEDS_MANAGE_GROUPS)
        return await self._repo.list_groups()

    async def get_group(self, user_id: str, group_id: str) -> QueryGroup:
        await self._require(user_id, Action.FEEDS_MANAGE_GROUPS)
        found = await self._repo.get_group(group_id)
        if found is None:
            raise NotFound(f"Query group {group_id} not found")
        return found

    async def create_group(self, user_id: str, **fields) -> QueryGroup:
        await self._require(user_id, Action.FEEDS_MANAGE_GROUPS)
        slug = self._validate_slug(fields.get("slug") or "")
        if await self._repo.get_group_by_slug(slug) is not None:
            raise Conflict(f"A query group with the slug '{slug}' already exists")
        return await self._repo.create_group(QueryGroup(
            slug=slug,
            name=(fields.get("name") or "").strip() or slug,
            description=fields.get("description") or "",
            sort_order=int(fields.get("sort_order") or 0),
            visibility=self._check_visibility(fields.get("visibility") or "public"),
        ))

    @staticmethod
    def _check_visibility(visibility: str) -> str:
        if visibility not in ("public", "admin"):
            raise InvalidInput("Visibility must be 'public' or 'admin'")
        return visibility

    async def update_group(self, user_id: str, group_id: str, **fields) -> QueryGroup:
        await self._require(user_id, Action.FEEDS_MANAGE_GROUPS)
        current = await self._repo.get_group(group_id)
        if current is None:
            raise NotFound(f"Query group {group_id} not found")
        updated = deepcopy(current)
        if fields.get("slug") is not None:
            slug = self._validate_slug(fields["slug"])
            clash = await self._repo.get_group_by_slug(slug)
            if clash is not None and clash.id != group_id:
                raise Conflict(f"A query group with the slug '{slug}' already exists")
            updated.slug = slug
        for key in ("name", "description"):
            if fields.get(key) is not None:
                setattr(updated, key, fields[key])
        if fields.get("sort_order") is not None:
            updated.sort_order = int(fields["sort_order"])
        if fields.get("visibility") is not None:
            updated.visibility = self._check_visibility(fields["visibility"])
        return await self._repo.update_group(updated)

    async def delete_group(self, user_id: str, group_id: str) -> None:
        await self._require(user_id, Action.FEEDS_MANAGE_GROUPS)
        await self._repo.delete_group(group_id)

    async def set_group_queries(
        self, user_id: str, group_id: str, query_ids: list[str],
    ) -> QueryGroup:
        await self._require(user_id, Action.FEEDS_MANAGE_GROUPS)
        if await self._repo.get_group(group_id) is None:
            raise NotFound(f"Query group {group_id} not found")
        seen: list[str] = []
        for query_id in query_ids or []:
            if query_id in seen:
                # Idempotent rather than an error: the same query twice in one
                # group is meaningless, not a mistake worth blocking a save on.
                continue
            if await self._repo.get_query(query_id) is None:
                raise NotFound(f"Named query {query_id} not found")
            seen.append(query_id)
        await self._repo.set_group_queries(group_id, seen)
        return await self.get_group(user_id, group_id)

    async def groups_for_query(self, user_id: str, query_id: str) -> list[QueryGroup]:
        await self._require(user_id, Action.FEEDS_MANAGE_QUERIES)
        return await self._repo.groups_for_query(query_id)

    # ── public catalogue ─────────────────────────────────────
    async def public_catalogue(self) -> list[QueryGroup]:
        """Published queries, in their public groups. No auth: this is what
        the feed picker renders for anyone."""
        groups = await self._repo.list_groups(visibility="public")
        out = []
        for group in groups:
            group.queries = [q for q in group.queries if q.status == "published"]
            if group.queries:
                out.append(group)
        return out
