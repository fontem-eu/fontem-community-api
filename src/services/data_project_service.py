"""Data Studio service — data projects with queries and plots.

A project is owned by its creator. Attaching it to an investigation lets that
investigation's members inherit access by role (viewer→read, contributor→edit,
owner→own), and per-user grants (viewer/commenter/editor/owner) add on top —
the exact model used by visualizations and dossiers, routed through
``AuthorizationService``. Query execution is NOT here: the browser runs the
read-only proxy queries + the DuckDB combine; the server only persists the
re-runnable recipes.
"""
from __future__ import annotations

from src.domain.data_project import DataPlot, DataProject, DataQuery
from src.repositories.data_project_repository import DataProjectRepository
from src.repositories.investigation_repository import InvestigationRepository
from src.repositories.resource_grant_repository import ResourceGrantRepository
from src.repositories.user_repository import UserRepository
from src.services.authz import Action, AuthorizationService, ResourceRef
from src.services.effective_access import _effective_access
from src.services.share_targets import resolve_share_target
from src.services.exceptions import InvalidInput, NotFound
from src.services.permission_service import LEVEL_HIERARCHY

_RESOURCE = "data_project"

# Access flags for a project the caller owns outright (list-mine / create).
OWNER_FLAGS = {"level": "owner", "can_edit": True, "can_delete": True, "can_share": True}


def _clean(name: str, fallback: str) -> str:
    return (name or "").strip()[:300] or fallback


class DataProjectService:
    def __init__(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        repo: DataProjectRepository,
        investigations: InvestigationRepository,
        authz: AuthorizationService,
        grants: ResourceGrantRepository,
        users: UserRepository,
    ) -> None:
        self._repo = repo
        self._inv = investigations
        self._authz = authz
        self._grants = grants
        self._users = users

    async def _load(self, project_id: str) -> DataProject:
        project = await self._repo.get_project(project_id)
        if project is None:
            raise NotFound(f"Data project {project_id} not found")
        return project

    async def _ref(self, user_id: str, project: DataProject) -> ResourceRef:
        """Build the policy's view of a project for this caller: the resource
        plus the caller's inherited investigation role and any direct grant."""
        member_role = None
        if project.investigation_id:
            m = await self._inv.get_member(project.investigation_id, user_id)
            member_role = m.role if m is not None else None
        grant = await self._grants.get_level(_RESOURCE, project.id, user_id)  # type: ignore[arg-type]
        return ResourceRef.for_data_project(
            project, member_role=member_role, effective_grant=grant)

    async def _require_project(
        self, user_id: str, project: DataProject, action: Action
    ) -> None:
        """Gate an action on a loaded project through the authz policy: owner →
        inherited investigation role → direct grant (additive, max wins)."""
        principal = await self._authz.principal(user_id)
        await self._authz.require(principal, action, await self._ref(user_id, project))

    async def access_flags(self, user_id: str, project: DataProject) -> dict:
        """The caller's capabilities on a project, for UI gating (read-only mode
        + which buttons to show). Non-auditing — the real gate is each mutation.
        Mirrors the policy exactly by probing the same actions."""
        principal = await self._authz.principal(user_id)
        ref = await self._ref(user_id, project)
        can_edit = self._authz.can_do(principal, Action.DATA_PROJECTS_EDIT, ref)
        can_delete = self._authz.can_do(principal, Action.DATA_PROJECTS_DELETE, ref)
        can_share = self._authz.can_do(principal, Action.DATA_PROJECTS_SHARE, ref)
        if can_delete:
            level = "owner"
        elif can_edit:
            level = "editor"
        else:
            level = "viewer"
        return {
            "level": level, "can_edit": can_edit,
            "can_delete": can_delete, "can_share": can_share,
        }

    async def _require_inv(self, user_id: str, investigation_id: str, action: Action) -> None:
        inv = await self._inv.get_by_id(investigation_id)
        if inv is None:
            raise NotFound(f"Investigation {investigation_id} not found")
        membership = await self._inv.get_member(investigation_id, user_id)
        principal = await self._authz.principal(user_id)
        await self._authz.require(
            principal, action, ResourceRef.for_investigation(inv, membership),
        )

    @staticmethod
    def _find_query(project: DataProject, query_id: str) -> DataQuery:
        for q in project.queries:
            if q.id == query_id:
                return q
        raise NotFound(f"Query {query_id} not found")

    @staticmethod
    def _find_plot(project: DataProject, plot_id: str) -> DataPlot:
        for p in project.plots:
            if p.id == plot_id:
                return p
        raise NotFound(f"Plot {plot_id} not found")

    # ── projects ────────────────────────────────────────────────
    async def list_projects(self, user_id: str) -> list[DataProject]:
        return await self._repo.list_for_user(user_id)

    async def list_for_investigation(
        self, user_id: str, investigation_id: str
    ) -> list[DataProject]:
        await self._require_inv(user_id, investigation_id, Action.INVESTIGATIONS_READ)
        return await self._repo.list_by_investigation(investigation_id)

    async def get_project(self, user_id: str, project_id: str) -> DataProject:
        project = await self._load(project_id)
        await self._require_project(user_id, project, Action.DATA_PROJECTS_READ)
        return project

    async def create_project(
        self, user_id: str, name: str, investigation_id: str | None = None
    ) -> DataProject:
        principal = await self._authz.principal(user_id)
        await self._authz.require(principal, Action.DATA_PROJECTS_CREATE)
        if investigation_id is not None:
            await self._require_inv(
                user_id, investigation_id, Action.INVESTIGATIONS_ADD_DATA_PROJECT
            )
        return await self._repo.create_project(DataProject(
            name=_clean(name, "Untitled project"), created_by=user_id,
            investigation_id=investigation_id,
        ))

    async def rename_project(self, user_id: str, project_id: str, name: str) -> DataProject:
        project = await self._load(project_id)
        await self._require_project(user_id, project, Action.DATA_PROJECTS_EDIT)
        project.name = _clean(name, project.name)
        return await self._repo.update_project(project)

    async def delete_project(self, user_id: str, project_id: str) -> None:
        project = await self._load(project_id)
        await self._require_project(user_id, project, Action.DATA_PROJECTS_DELETE)
        await self._repo.delete_project(project_id)

    # ── investigation attach / sharing ──────────────────────────
    async def attach(self, user_id: str, project_id: str, investigation_id: str) -> None:
        project = await self._load(project_id)
        await self._require_project(user_id, project, Action.DATA_PROJECTS_EDIT)
        await self._require_inv(user_id, investigation_id, Action.INVESTIGATIONS_ADD_DATA_PROJECT)
        await self._repo.set_investigation(project_id, investigation_id)

    async def detach(self, user_id: str, project_id: str) -> None:
        project = await self._load(project_id)
        await self._require_project(user_id, project, Action.DATA_PROJECTS_EDIT)
        await self._repo.set_investigation(project_id, None)

    async def share(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self, user_id: str, project_id: str,
        target_user_id: str | None = None, *, target_email: str | None = None,
        level: str = "viewer",
    ) -> None:
        project = await self._load(project_id)
        await self._require_project(user_id, project, Action.DATA_PROJECTS_SHARE)
        if level not in LEVEL_HIERARCHY:
            raise InvalidInput(
                f"'{level}' is not a valid access level (viewer, commenter, editor, owner)."
            )
        target = await resolve_share_target(self._users, target_user_id, target_email)
        if target is not None:  # unknown email -> uniform no-op (no enumeration oracle)
            await self._grants.set_grant(_RESOURCE, project_id, target, level)

    async def revoke(self, user_id: str, project_id: str, target_user_id: str) -> None:
        project = await self._load(project_id)
        await self._require_project(user_id, project, Action.DATA_PROJECTS_SHARE)
        await self._grants.remove_grant(_RESOURCE, project_id, target_user_id)

    async def list_grants(self, user_id: str, project_id: str) -> list[dict]:
        project = await self._load(project_id)
        await self._require_project(user_id, project, Action.DATA_PROJECTS_READ)
        out = []
        for g in await self._grants.list_grants(_RESOURCE, project_id):
            u = await self._users.get_by_id(g.user_id)
            out.append({
                "user_id": g.user_id, "level": g.level,
                "email": u.email if u else None, "name": u.name if u else None,
            })
        return out

    async def effective_access(self, user_id: str, project_id: str) -> list[dict]:
        """Who has access and why (owner / inherited:<role> / direct). READ-gated."""
        project = await self._load(project_id)
        await self._require_project(user_id, project, Action.DATA_PROJECTS_READ)
        return await _effective_access(
            self._inv, self._grants, self._users, _RESOURCE, project_id,
            project.created_by, project.investigation_id,
        )


    # ── queries ─────────────────────────────────────────────────
    async def add_query(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self, user_id: str, project_id: str, name: str, lang: str, query: str,
    ) -> DataQuery:
        project = await self._load(project_id)
        await self._require_project(user_id, project, Action.DATA_PROJECTS_EDIT)
        return await self._repo.add_query(DataQuery(
            project_id=project_id, name=_clean(name, f"Query {len(project.queries) + 1}"),
            lang=lang or "cypher", query=query or "", sort_order=len(project.queries),
        ))

    async def update_query(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self, user_id: str, project_id: str, query_id: str,
        name: str | None, lang: str | None, query: str | None,
    ) -> DataQuery:
        project = await self._load(project_id)
        await self._require_project(user_id, project, Action.DATA_PROJECTS_EDIT)
        existing = self._find_query(project, query_id)
        if name is not None:
            existing.name = _clean(name, existing.name)
        if lang is not None:
            existing.lang = lang
        if query is not None:
            existing.query = query
        return await self._repo.update_query(existing)

    async def delete_query(self, user_id: str, project_id: str, query_id: str) -> None:
        project = await self._load(project_id)
        await self._require_project(user_id, project, Action.DATA_PROJECTS_EDIT)
        self._find_query(project, query_id)
        await self._repo.delete_query(query_id)

    async def duplicate_query(self, user_id: str, project_id: str, query_id: str) -> DataQuery:
        project = await self._load(project_id)
        await self._require_project(user_id, project, Action.DATA_PROJECTS_EDIT)
        src = self._find_query(project, query_id)
        return await self._repo.add_query(DataQuery(
            project_id=project_id, name=f"{src.name} copy", lang=src.lang, query=src.query,
            sort_order=len(project.queries),
        ))

    # ── plots ───────────────────────────────────────────────────
    async def add_plot(self, user_id: str, project_id: str, name: str, spec: dict) -> DataPlot:
        project = await self._load(project_id)
        await self._require_project(user_id, project, Action.DATA_PROJECTS_EDIT)
        return await self._repo.add_plot(DataPlot(
            project_id=project_id, name=_clean(name, f"Plot {len(project.plots) + 1}"),
            spec=spec or {}, sort_order=len(project.plots),
        ))

    async def update_plot(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self, user_id: str, project_id: str, plot_id: str, name: str | None, spec: dict | None,
    ) -> DataPlot:
        project = await self._load(project_id)
        await self._require_project(user_id, project, Action.DATA_PROJECTS_EDIT)
        existing = self._find_plot(project, plot_id)
        if name is not None:
            existing.name = _clean(name, existing.name)
        if spec is not None:
            existing.spec = spec
        return await self._repo.update_plot(existing)

    async def delete_plot(self, user_id: str, project_id: str, plot_id: str) -> None:
        project = await self._load(project_id)
        await self._require_project(user_id, project, Action.DATA_PROJECTS_EDIT)
        self._find_plot(project, plot_id)
        await self._repo.delete_plot(plot_id)
