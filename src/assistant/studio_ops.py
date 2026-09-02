"""Data Studio operations, executed server-side as the asking user.

The first cut of these tools emitted proposals for the browser to perform,
on the reasoning that the tool executor has no user identity. That was true
of the *generated* tools — they GET fontem-api anonymously — but not of this
service, which is holding the user's id for the whole turn and can call the
Studio service directly.

Direct is better here. The service enforces access on every call, so the
agent inherits exactly the permissions the user has and no more; there is no
round trip through the browser to lose; and reading is possible at all,
which a propose-only design could never offer — an agent that cannot list
what already exists writes a second project rather than adding to the first.

Approval stays a UI concern. Nothing here is destructive: no delete, and
edits are addressed by id, so the worst a confused turn does is add a query
nobody asked for.

Results are shaped for a model, not for a UI. Ids are included because the
next call needs them, timestamps are dropped because nothing does, and query
text is truncated in listings — a project with ten 8000-character queries
would otherwise spend a whole turn's budget on one call.
"""
from __future__ import annotations

import json
from typing import Any

from src.services import studio_validation

#: One run may not eat the whole turn: same order as any single
#: tool result, minus headroom for the envelope around it.
_RESULT_CHARS = 7_500

#: Query text is long and rarely needed in full when listing. Reading one
#: query in full is what get_project's `query_id` filter is for.
_QUERY_PREVIEW_CHARS = 400


class StudioOps:
    """The Studio surface, bound to one user for one turn."""

    def __init__(self, service: Any, user_id: str) -> None:
        self._svc = service
        self._user = user_id
        # Set per call by `execute`. The HTTP client belongs to the turn, not
        # to this object: it is the one the tool dispatch already opened, and
        # opening a second one per validation would double the connections a
        # turn holds.
        self._client: Any = None
        self._api_url: str = ""

    # ── validation ─────────────────────────────────────────────
    #
    # An agent that is told "created" learns nothing from a query that does
    # not parse, so it moves on and the project keeps a broken query. These
    # check before writing and hand the engine's own words back.

    async def _check_query(self, lang: str, query: str):
        """Validate, or return None when there is nothing to validate with."""
        if self._client is None:
            return None
        return await studio_validation.validate_query(
            self._client, self._api_url, lang, query)

    async def _check_plot(self, spec: dict):
        if self._client is None:
            return None
        return await studio_validation.validate_plot(
            self._client, self._api_url, spec or {})

    @staticmethod
    def _refusal(verdict, subject: str) -> dict:
        return {
            "error": f"the {subject} was not saved because it does not work",
            **verdict.as_dict(),
            "hint": f"fix the {subject} and call this tool again",
        }

    @staticmethod
    def _with_notes(result: dict, verdict) -> dict:
        """Attach anything worth knowing about a write that did go through.

        Only when there is something to say: a clean check adds nothing to
        the payload, because "valid: true" on every successful call is noise
        the model pays for on every turn.
        """
        if verdict is not None and (verdict.warnings or not verdict.checked):
            result = dict(result)
            result["validation"] = verdict.as_dict()
        return result

    # ── shaping ────────────────────────────────────────────────
    @staticmethod
    def _query_dict(q: Any, *, full: bool) -> dict:
        text = getattr(q, "query", "") or ""
        return {
            "id": getattr(q, "id", None),
            "name": getattr(q, "name", ""),
            "lang": getattr(q, "lang", ""),
            "query": text if full or len(text) <= _QUERY_PREVIEW_CHARS
                     else text[:_QUERY_PREVIEW_CHARS] + " …[truncated, ask for this query by id]",
        }

    @staticmethod
    def _plot_dict(p: Any) -> dict:
        return {
            "id": getattr(p, "id", None),
            "name": getattr(p, "name", ""),
            "spec": getattr(p, "spec", {}) or {},
        }

    def _project_dict(self, p: Any, *, deep: bool, full_query: str | None = None) -> dict:
        out = {
            "id": getattr(p, "id", None),
            "name": getattr(p, "name", ""),
            "investigation_id": getattr(p, "investigation_id", None),
        }
        if deep:
            out["queries"] = [
                self._query_dict(q, full=(full_query is not None
                                          and getattr(q, "id", None) == full_query))
                for q in getattr(p, "queries", []) or []
            ]
            out["plots"] = [self._plot_dict(x) for x in getattr(p, "plots", []) or []]
        else:
            out["queries"] = len(getattr(p, "queries", []) or [])
            out["plots"] = len(getattr(p, "plots", []) or [])
        return out

    # ── operations ─────────────────────────────────────────────
    async def list_projects(self, **_) -> dict:
        projects = await self._svc.list_projects(self._user)
        return {"projects": [self._project_dict(p, deep=False) for p in projects]}

    async def get_project(self, project_id: str = "", query_id: str = "", **_) -> dict:
        project = await self._svc.get_project(self._user, project_id)
        return self._project_dict(project, deep=True, full_query=query_id or None)

    async def run_query(self, project_id: str = "", query_id: str = "", **_) -> dict:
        """Execute a saved query and return its rows.

        The missing verb. The model could create and update queries but never
        see what they returned — in the sessions that motivated this it wrote
        six queries, including two schema probes, and read back exactly
        nothing from any of them.

        Execution goes through the same read-only, size- and row-capped
        proxies the Studio's own Run button uses (fontem-api /query/*,
        /sparql). Nothing model-facing gets a weaker guarantee than the
        button, and there is no second query engine to diverge from the
        first.
        """
        if self._client is None:
            return {"error": "no query engine is reachable this turn",
                    "hint": "the saved query is intact; run it again later"}
        query = await self._existing_query(project_id, query_id)
        if query is None:
            return {"error": f"no query {query_id!r} in project {project_id!r}",
                    "hint": "studio_get_project lists the ids"}
        lang = (getattr(query, "lang", "") or "cypher").strip().lower()
        path = studio_validation.QUERY_PATHS.get(lang)
        if path is None:
            return {"error": f"stored query has unknown language {lang!r}"}
        try:
            resp = await self._client.post(
                self._api_url.rstrip("/") + path,
                json={"query": getattr(query, "query", "") or ""},
                timeout=30.0,
            )
        except Exception as exc:  # pylint: disable=broad-except
            return {"error": f"the {lang} engine did not answer: "
                             f"{type(exc).__name__}"}
        if resp.status_code >= 400:
            return {"error": f"the {lang} engine rejected the query "
                             f"(HTTP {resp.status_code})",
                    "detail": resp.text[:600]}
        body = resp.text
        if len(body) > _RESULT_CHARS:
            body = (body[:_RESULT_CHARS]
                    + f' …[truncated at {_RESULT_CHARS} characters; narrow '
                      f'the query to see the rest]')
        return {"query_id": getattr(query, "id", None),
                "name": getattr(query, "name", ""), "lang": lang,
                "result": body}

    async def create_project(self, name: str = "", investigation_id: str = "", **_) -> dict:
        project = await self._svc.create_project(
            self._user, name, investigation_id or None)
        return self._project_dict(project, deep=False)

    async def rename_project(self, project_id: str = "", name: str = "", **_) -> dict:
        project = await self._svc.rename_project(self._user, project_id, name)
        return self._project_dict(project, deep=False)

    async def add_query(self, project_id: str = "", name: str = "",
                        lang: str = "cypher", query: str = "", **_) -> dict:
        verdict = await self._check_query(lang, query)
        if verdict is not None and not verdict.ok:
            return self._refusal(verdict, "query")
        created = await self._svc.add_query(
            self._user, project_id, name, lang, query)
        result = self._query_dict(created, full=True)
        # Both a 1.7B and a 30B wrote queries and never ran them — they
        # reached for the ad-hoc probe instead, or stopped. The affordance
        # rides in the result, where the model is already looking.
        result["next"] = (
            f"run it: studio_run_query(project_id={project_id!r}, "
            f"query_id={result['id']!r})")
        return self._with_notes(result, verdict)

    async def update_query(self, project_id: str = "", query_id: str = "",
                           name: str | None = None, lang: str | None = None,
                           query: str | None = None, **_) -> dict:
        verdict = None
        if query is not None:
            # The language may not be changing, in which case the stored one
            # is what this query will run under — validating against the
            # default instead would check something nobody will execute.
            effective_lang = lang
            if effective_lang is None:
                existing = await self._existing_query(project_id, query_id)
                effective_lang = getattr(existing, "lang", None) or "cypher"
            verdict = await self._check_query(effective_lang, query)
            if verdict is not None and not verdict.ok:
                return self._refusal(verdict, "query")
        updated = await self._svc.update_query(
            self._user, project_id, query_id, name, lang, query)
        return self._with_notes(self._query_dict(updated, full=True), verdict)

    async def _existing_query(self, project_id: str, query_id: str):
        """The stored query, or None. Best-effort: a lookup that fails must
        not block a write the service itself would have allowed."""
        try:
            project = await self._svc.get_project(self._user, project_id)
        except Exception:  # pylint: disable=broad-except
            return None
        for q in getattr(project, "queries", []) or []:
            if getattr(q, "id", None) == query_id:
                return q
        return None

    async def add_plot(self, project_id: str = "", name: str = "",
                       spec: dict | None = None, **_) -> dict:
        verdict = await self._check_plot(spec or {})
        if verdict is not None and not verdict.ok:
            return self._refusal(verdict, "plot")
        created = await self._svc.add_plot(self._user, project_id, name, spec or {})
        return self._with_notes(self._plot_dict(created), verdict)

    async def update_plot(self, project_id: str = "", plot_id: str = "",
                          name: str | None = None, spec: dict | None = None, **_) -> dict:
        verdict = None
        if spec is not None:
            verdict = await self._check_plot(spec)
            if verdict is not None and not verdict.ok:
                return self._refusal(verdict, "plot")
        updated = await self._svc.update_plot(
            self._user, project_id, plot_id, name, spec)
        return self._with_notes(self._plot_dict(updated), verdict)

    async def plot_recipe(self, project_id: str, plot_id: str) -> dict:
        """One saved plot, shaped as the widget the editor embeds.

        The Studio stores a plot as a `spec`; the article embeds it as a
        `pipeline` widget — `data_params` (the sources and the DuckDB
        transform) plus `ui_params` (which chart, which columns). The
        translation between the two already existed in the browser, in
        StudioPlotView's Pocket button. Doing it here as well means the
        assistant proposes exactly what the Pocket button produces, so
        both roads end at one renderer rather than two.

        Ownership comes for free: `_svc.get_project` is scoped to the
        asking user, so a plot id belonging to somebody else resolves to
        nothing rather than to a chart.
        """
        project = await self._svc.get_project(self._user, project_id)
        plots = getattr(project, "plots", None) or []
        plot = next((x for x in plots if str(getattr(x, "id", "")) == plot_id), None)
        if plot is None:
            return {"error": f"no plot {plot_id!r} in project {project_id!r}"}
        spec = dict(getattr(plot, "spec", None) or {})
        chart = spec.get("chart")
        if not chart:
            return {"error": f"plot {plot_id!r} has no chart configured",
                    "hint": "open it in the Studio and pick a chart type"}
        if not (spec.get("sources") or []):
            return {"error": f"plot {plot_id!r} has no data sources",
                    "hint": "the plot cannot re-run without at least one source"}
        ui = {"chart": chart, "x": spec.get("x", ""), "y": spec.get("y", ""),
              "y2": spec.get("y2", ""), "level": spec.get("level", 0),
              "bivariate": spec.get("bivariate", "none"),
              "series": list(spec.get("series") or []),
              "corrCols": list(spec.get("corrCols") or [])}
        if spec.get("events"):
            ui["events"] = spec["events"]
        return {
            "name": getattr(plot, "name", "") or "Studio plot",
            "data_params": {"sources": list(spec.get("sources") or []),
                            "transform": spec.get("transform", "")},
            "ui_params": ui,
        }

    # ── dispatch ───────────────────────────────────────────────
    #: Tool name -> method. No delete: an agent that can remove a user's work
    #: is a different risk conversation, and nothing here needs it.
    OPS = {
        "mcp__gmr__studio_list_projects": "list_projects",
        "mcp__gmr__studio_get_project": "get_project",
        "mcp__gmr__studio_run_query": "run_query",
        "mcp__gmr__studio_create_project": "create_project",
        "mcp__gmr__studio_rename_project": "rename_project",
        "mcp__gmr__studio_add_query": "add_query",
        "mcp__gmr__studio_update_query": "update_query",
        "mcp__gmr__studio_add_plot": "add_plot",
        "mcp__gmr__studio_update_plot": "update_plot",
    }

    async def execute(self, name: str, args: dict, *,
                      client: Any = None, api_url: str = "") -> str:
        """Run one Studio tool. Always returns a JSON string, never raises.

        A raised exception here would abort the turn mid-stream; the model
        can act on an error it can read — usually by fixing an id — and
        cannot act on a dropped connection.
        """
        method = self.OPS.get(name)
        if method is None:
            return json.dumps({"error": f"unknown studio tool: {name}"})
        # Borrowed for this call only. Without a client there is nothing to
        # validate against, and the write proceeds as it always did — that
        # is the case for every existing unit test, and for any caller that
        # has no fontem-api to ask.
        self._client, self._api_url = client, api_url
        try:
            result = await getattr(self, method)(**(args or {}))
            return json.dumps(result, default=str)
        except Exception as exc:  # pylint: disable=broad-except
            # Includes the service's own permission and not-found errors,
            # which are exactly what the model needs to see.
            return json.dumps({
                "error": f"{type(exc).__name__}: {exc}",
                "tool": name,
            }, default=str)
