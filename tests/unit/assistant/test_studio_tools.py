"""The Data Studio surface: what the model is offered, and what runs it.

These tools execute server-side as the asking user. The first cut emitted
proposals for the browser to perform, on the reasoning that the tool
executor has no identity — true of the generated tools, which GET
fontem-api anonymously, but not of this service, which holds the user's id
for the whole turn. Direct is better: the service enforces access per call,
and reading becomes possible at all. An agent that cannot list what exists
writes a second project instead of adding to the first.
"""
import json
import pathlib

import pytest

from src.assistant import studio_tools
from src.assistant.engine_tools import (
    OFFERED_BUILTINS, OFFERED_GENERATED, turn_tool_specs,
)
from src.assistant.studio_ops import StudioOps

ROUTES = [{"path": "/map", "description": "Atlas"}]
GENERATED = [{"function": {"name": n, "description": "d", "parameters": {}}}
             for n in ("get_series", "list_datasets", "get_doc",
                       "contract_sectors", "single_bidder_rate")]


def _names(specs):
    return [s["function"]["name"] for s in specs]


# ── the surface ────────────────────────────────────────────────
def test_the_offered_surface_is_deliberately_small():
    """A wide surface of near-misses is worse than a narrow one: it costs
    tokens every turn and gives a small model more ways to pick wrong."""
    got = _names(turn_tool_specs(GENERATED, True, ROUTES))
    assert got == [
        "navigate",
        "mcp__gmr__search_entities",
        "mcp__gmr__investigate_entity",
        "mcp__gmr__read_document",
        "mcp__gmr__set_title",
        "mcp__gmr__set_abstract",
        "mcp__gmr__replace_body",
        "mcp__gmr__insert_widget",
        "mcp__gmr__query_graph",
        "mcp__gmr__calculate",
        "mcp__gmr__studio_list_projects",
        "mcp__gmr__studio_get_project",
        "mcp__gmr__studio_run_query",
        "mcp__gmr__studio_create_project",
        "mcp__gmr__studio_rename_project",
        "mcp__gmr__studio_add_query",
        "mcp__gmr__studio_update_query",
        "mcp__gmr__studio_add_plot",
        "mcp__gmr__studio_update_plot",
        "get_doc",
    ]


def test_the_narrow_generated_endpoints_are_gone():
    """Eleven of these could not between them answer 'which contracts
    involve Israeli companies' while crowding the array."""
    got = _names(turn_tool_specs(GENERATED, True, ROUTES))
    for dropped in ("get_series", "list_datasets", "contract_sectors",
                    "single_bidder_rate"):
        assert dropped not in got
    # get_schema joined 2026-08-27: without it the model guessed the graph's
    # edge direction and got zero rows where the data lives. Still a curated
    # pair, not a drift back toward eleven.
    assert OFFERED_GENERATED == ("get_doc", "get_schema")


def test_find_paths_is_no_longer_advertised():
    """It needs two resolved ids before it can be called at all, and was
    being chosen over search_entities for questions search answers."""
    assert "mcp__gmr__find_paths" not in OFFERED_BUILTINS
    assert "mcp__gmr__find_paths" not in _names(
        turn_tool_specs(GENERATED, True, ROUTES))


def test_studio_tools_need_no_open_project():
    """They act on the user's own projects through the service, which checks
    access per call, so there is no state the caller has to be "in".

    Gating them on an open Studio was backwards: the agent has a tool to
    create a project, and the gate stopped it using that tool until the user
    had already done the thing they were asking for. The only genuinely
    UI-bound tool is propose_edit, which needs a surface to propose into.
    """
    without_editor = _names(turn_tool_specs(GENERATED, False, ROUTES))
    for name in studio_tools.STUDIO_ACTIONS:
        assert name in without_editor, f"{name} withheld with no editor open"
    assert "mcp__gmr__propose_edit" not in without_editor


def test_the_only_ui_gated_tool_is_the_one_that_needs_a_surface():
    with_editor = _names(turn_tool_specs(GENERATED, True, ROUTES))
    without = _names(turn_tool_specs(GENERATED, False, ROUTES))
    # The whole document surface needs the editor: reading a document you
    # cannot propose into invites edits with nowhere to land.
    assert set(with_editor) - set(without) == {
        "mcp__gmr__read_document",
        "mcp__gmr__set_title",
        "mcp__gmr__set_abstract",
        "mcp__gmr__replace_body",
        "mcp__gmr__insert_widget",
    }


def test_reading_comes_before_writing_in_the_array():
    """Order is a hint the model reads. list and get precede create, because
    adding to the project the user already has is almost always the intent."""
    got = _names(turn_tool_specs(GENERATED, True, ROUTES))
    assert got.index("mcp__gmr__studio_list_projects") < got.index(
        "mcp__gmr__studio_create_project")
    assert got.index("mcp__gmr__studio_get_project") < got.index(
        "mcp__gmr__studio_add_query")


def test_no_tool_can_delete_anything():
    """An agent that can remove a user's work is a different risk
    conversation, and nothing here needs it."""
    for name in list(studio_tools.STUDIO_ACTIONS) + list(StudioOps.OPS):
        assert "delete" not in name and "remove" not in name


def test_every_advertised_tool_has_an_implementation():
    """A schema with no operation behind it is a tool that always errors."""
    assert set(studio_tools.STUDIO_ACTIONS) == set(StudioOps.OPS)


def test_the_query_languages_and_charts_are_pinned():
    assert studio_tools.QUERY_LANGS == ("cypher", "sql", "sparql")
    by_name = {t["function"]["name"]: t for t in studio_tools.STUDIO_TOOLS}
    lang = by_name["mcp__gmr__studio_add_query"][
        "function"]["parameters"]["properties"]["lang"]
    assert lang["enum"] == list(studio_tools.QUERY_LANGS)
    spec = by_name["mcp__gmr__studio_add_plot"][
        "function"]["parameters"]["properties"]["spec"]
    assert spec["properties"]["chart"]["enum"] == list(studio_tools.CHART_TYPES)


def test_the_plot_tool_says_the_transform_is_duckdb():
    """A model writing Cypher in a transform gets a syntax error from an
    engine it never chose."""
    by_name = {t["function"]["name"]: t for t in studio_tools.STUDIO_TOOLS}
    desc = by_name["mcp__gmr__studio_add_plot"]["function"]["description"]
    assert "DuckDB" in desc


def test_the_schemas_are_json_serialisable():
    assert json.loads(json.dumps(studio_tools.STUDIO_TOOLS))


# ── execution ──────────────────────────────────────────────────
class _Recorder:
    """Stands in for DataProjectService, recording exactly what it was given.

    These tests are about the tools, not the model: whether an argument the
    model supplies arrives at the service unchanged, and whether a partial
    update stays partial. Neither is visible from the schema, and both are
    the kind of thing that fails silently — a dropped `lang` becomes a
    Cypher query run against Postgres.
    """

    def __init__(self):
        self.calls = []

    def _rec(self, _op, **kw):
        # Leading underscore deliberately: several operations pass a `name`
        # keyword, and a parameter called `name` here collides with it.
        self.calls.append((_op, kw))

    async def list_projects(self, user_id):
        self._rec("list_projects", user_id=user_id)
        return [_P("p1", "Procurement",
                   queries=[_Q("q1", "x" * 5000)], plots=[_Pl("pl1", "Chart")])]

    async def get_project(self, user_id, project_id):
        self._rec("get_project", user_id=user_id, project_id=project_id)
        return _P(project_id, "Procurement",
                  queries=[_Q("q1", "y" * 5000)], plots=[_Pl("pl1", "Chart")])

    async def create_project(self, user_id, name, investigation_id=None):
        self._rec("create_project", user_id=user_id, name=name,
                  investigation_id=investigation_id)
        return _P("new", name)

    async def rename_project(self, user_id, project_id, name):
        self._rec("rename_project", user_id=user_id,
                  project_id=project_id, name=name)
        return _P(project_id, name)

    async def add_query(self, user_id, project_id, name, lang, query):
        self._rec("add_query", user_id=user_id, project_id=project_id,
                  name=name, lang=lang, query=query)
        return _Q("q2", query, name=name, lang=lang)

    async def update_query(self, user_id, project_id, query_id, name, lang, query):
        self._rec("update_query", user_id=user_id, project_id=project_id,
                  query_id=query_id, name=name, lang=lang, query=query)
        return _Q(query_id, query or "kept", name=name or "kept",
                  lang=lang or "cypher")

    async def add_plot(self, user_id, project_id, name, spec):
        self._rec("add_plot", user_id=user_id, project_id=project_id,
                  name=name, spec=spec)
        return _Pl("pl2", name, spec)

    async def update_plot(self, user_id, project_id, plot_id, name, spec):
        self._rec("update_plot", user_id=user_id, project_id=project_id,
                  plot_id=plot_id, name=name, spec=spec)
        return _Pl(plot_id, name or "kept", spec or {})

    def last(self, name):
        return next(kw for called, kw in reversed(self.calls) if called == name)


class _P:
    def __init__(self, pid, name, queries=None, plots=None):
        self.id, self.name = pid, name
        self.investigation_id = None
        self.queries = queries or []
        self.plots = plots or []


class _Q:
    def __init__(self, qid, query, name="Q", lang="cypher"):
        self.id, self.query, self.name, self.lang = qid, query, name, lang


class _Pl:
    def __init__(self, pid, name, spec=None):
        self.id, self.name, self.spec = pid, name, spec or {}


async def _run(svc, tool, args):
    """Execute one tool and return its parsed result."""
    return json.loads(await StudioOps(svc, "user-42").execute(
        f"mcp__gmr__studio_{tool}", args))


# ── one test per operation ─────────────────────────────────────
@pytest.mark.asyncio
async def test_every_operation_runs_as_the_asking_user():
    """The service checks access per call, so the agent inherits exactly the
    user's permissions — but only if the id is threaded through every one."""
    svc = _Recorder()
    ops = StudioOps(svc, "user-42")
    for tool, args in (
        ("list_projects", {}),
        ("get_project", {"project_id": "p1"}),
        ("create_project", {"name": "N"}),
        ("rename_project", {"project_id": "p1", "name": "N"}),
        ("add_query", {"project_id": "p1", "name": "Q", "lang": "sql", "query": "SELECT 1"}),
        ("update_query", {"project_id": "p1", "query_id": "q1", "name": "R"}),
        ("add_plot", {"project_id": "p1", "name": "P", "spec": {"chart": "bar"}}),
        ("update_plot", {"project_id": "p1", "plot_id": "pl1", "name": "P2"}),
    ):
        await ops.execute(f"mcp__gmr__studio_{tool}", args)
    assert len(svc.calls) == 8, "an operation never reached the service"
    assert {kw["user_id"] for _n, kw in svc.calls} == {"user-42"}


@pytest.mark.asyncio
async def test_list_projects_reports_counts_not_contents():
    """A listing that inlined every query would spend the turn's budget
    before the model had chosen a project."""
    out = await _run(_Recorder(), "list_projects", {})
    project = out["projects"][0]
    assert project["queries"] == 1 and project["plots"] == 1
    assert project["id"] == "p1"


@pytest.mark.asyncio
async def test_get_project_returns_the_ids_the_next_call_needs():
    """Every edit is addressed by id. A read that omits them makes the write
    tools unreachable."""
    out = await _run(_Recorder(), "get_project", {"project_id": "p1"})
    assert out["queries"][0]["id"] == "q1"
    assert out["plots"][0]["id"] == "pl1"
    assert out["queries"][0]["lang"] == "cypher"


@pytest.mark.asyncio
async def test_get_project_abbreviates_query_text_but_says_so():
    out = await _run(_Recorder(), "get_project", {"project_id": "p1"})
    text = out["queries"][0]["query"]
    assert len(text) < 600
    assert "truncated" in text and "by id" in text


@pytest.mark.asyncio
async def test_get_project_returns_one_query_in_full_on_request():
    out = await _run(_Recorder(), "get_project",
                     {"project_id": "p1", "query_id": "q1"})
    assert len(out["queries"][0]["query"]) == 5000


@pytest.mark.asyncio
async def test_create_project_passes_the_name_and_omits_an_empty_investigation():
    """An empty string is not an investigation id. Forwarding "" would attach
    the project to nothing and fail the lookup."""
    svc = _Recorder()
    await _run(svc, "create_project", {"name": "Hungary bids"})
    assert svc.last("create_project")["name"] == "Hungary bids"
    assert svc.last("create_project")["investigation_id"] is None

    await _run(svc, "create_project",
               {"name": "N", "investigation_id": "inv-7"})
    assert svc.last("create_project")["investigation_id"] == "inv-7"


@pytest.mark.asyncio
async def test_rename_project_passes_both_ids():
    svc = _Recorder()
    out = await _run(svc, "rename_project", {"project_id": "p1", "name": "Better"})
    assert svc.last("rename_project") == {
        "user_id": "user-42", "project_id": "p1", "name": "Better"}
    assert out["name"] == "Better"


@pytest.mark.asyncio
async def test_add_query_forwards_the_language_verbatim():
    """A dropped `lang` is a Cypher query run against Postgres — an error the
    model cannot read its way out of, because it wrote valid Cypher."""
    svc = _Recorder()
    out = await _run(svc, "add_query", {
        "project_id": "p1", "name": "Offences",
        "lang": "sql", "query": "SELECT * FROM observation LIMIT 5"})
    call = svc.last("add_query")
    assert call["lang"] == "sql"
    assert call["query"] == "SELECT * FROM observation LIMIT 5"
    assert call["name"] == "Offences"
    assert out["id"] == "q2"


@pytest.mark.asyncio
async def test_add_query_returns_the_text_in_full():
    """The model just wrote it; abbreviating its own query back at it is
    noise, and the id is what the next call needs."""
    out = await _run(_Recorder(), "add_query", {
        "project_id": "p1", "name": "Q", "lang": "cypher", "query": "z" * 5000})
    assert len(out["query"]) == 5000


@pytest.mark.asyncio
async def test_update_query_leaves_omitted_fields_alone():
    """Partial update semantics. Sending None for `query` must mean "keep
    it", not "blank it" — the service distinguishes the two and the tool has
    to preserve that distinction."""
    svc = _Recorder()
    await _run(svc, "update_query", {
        "project_id": "p1", "query_id": "q1", "name": "Renamed"})
    call = svc.last("update_query")
    assert call["name"] == "Renamed"
    assert call["query"] is None, "an omitted query would have been blanked"
    assert call["lang"] is None


@pytest.mark.asyncio
async def test_add_plot_forwards_the_spec_intact():
    """The spec is the whole chart. A flattened or dropped key is a plot that
    renders empty with no error anywhere."""
    spec = {"sources": ["q1", "q2"], "transform": "SELECT * FROM q1",
            "chart": "line", "x": "year", "y": "value", "series": "country"}
    svc = _Recorder()
    out = await _run(svc, "add_plot",
                     {"project_id": "p1", "name": "Trend", "spec": spec})
    assert svc.last("add_plot")["spec"] == spec
    assert out["spec"] == spec
    assert out["id"] == "pl2"


@pytest.mark.asyncio
async def test_add_plot_without_a_spec_sends_an_empty_dict_not_none():
    svc = _Recorder()
    await _run(svc, "add_plot", {"project_id": "p1", "name": "Empty"})
    assert svc.last("add_plot")["spec"] == {}


@pytest.mark.asyncio
async def test_update_plot_leaves_omitted_fields_alone():
    svc = _Recorder()
    await _run(svc, "update_plot",
               {"project_id": "p1", "plot_id": "pl1", "name": "Renamed"})
    call = svc.last("update_plot")
    assert call["name"] == "Renamed"
    assert call["spec"] is None, "an omitted spec would have wiped the chart"


@pytest.mark.asyncio
async def test_every_operation_returns_parseable_json():
    """The result goes straight into the conversation. A non-serialisable
    object would raise inside the tool loop and kill the turn."""
    svc = _Recorder()
    ops = StudioOps(svc, "u")
    for tool, args in (
        ("list_projects", {}), ("get_project", {"project_id": "p1"}),
        ("create_project", {"name": "N"}),
        ("rename_project", {"project_id": "p1", "name": "N"}),
        ("add_query", {"project_id": "p1", "name": "Q", "lang": "sql", "query": "S"}),
        ("update_query", {"project_id": "p1", "query_id": "q1"}),
        ("add_plot", {"project_id": "p1", "name": "P", "spec": {}}),
        ("update_plot", {"project_id": "p1", "plot_id": "pl1"}),
    ):
        raw = await ops.execute(f"mcp__gmr__studio_{tool}", args)
        assert isinstance(json.loads(raw), dict), tool


@pytest.mark.asyncio
async def test_a_failure_returns_an_error_the_model_can_act_on():
    """A raised exception aborts the turn mid-stream. The model can fix a bad
    id if told; it can do nothing with a dropped connection."""
    class Boom:
        async def get_project(self, *_a, **_k):
            raise PermissionError("not your project")

    out = json.loads(await StudioOps(Boom(), "u").execute(
        "mcp__gmr__studio_get_project", {"project_id": "nope"}))
    assert "PermissionError" in out["error"]
    assert out["tool"] == "mcp__gmr__studio_get_project"


@pytest.mark.asyncio
async def test_an_unexpected_argument_does_not_crash_the_turn():
    """Models invent parameters. Swallowing one is better than failing the
    turn over it, and the schema is what steers them."""
    out = await _run(_Recorder(), "list_projects", {"nonsense": 1})
    assert "projects" in out


@pytest.mark.asyncio
async def test_an_unknown_tool_is_reported_not_raised():
    out = json.loads(await StudioOps(_Recorder(), "u").execute("nope", {}))
    assert "unknown studio tool" in out["error"]


def test_every_engine_routes_studio_calls_to_the_ops():
    """Not to the HTTP executor, which is anonymous and read-only.

    The routing moved into ToolRuntime.dispatch when the hand-written
    executor was decommissioned — one implementation both engines call —
    so the assertion follows it there rather than grepping each engine for
    a call it now delegates.
    """
    runtime = pathlib.Path("src/assistant/tool_runtime.py").read_text("utf-8")
    assert "studio.execute(" in runtime, "the shared dispatch does not use the ops"
    for mod in ("langgraph_client", "pydantic_ai_client"):
        src = pathlib.Path(f"src/assistant/{mod}.py").read_text("utf-8")
        assert "studio_ops" in src, f"{mod} never receives them"
        assert "dispatch(" in src, f"{mod} does not route through the runtime"
