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
    got = _names(turn_tool_specs(GENERATED, True, ROUTES, has_studio=True))
    assert got == [
        "navigate",
        "mcp__gmr__search_entities",
        "mcp__gmr__investigate_entity",
        "mcp__gmr__propose_edit",
        "mcp__gmr__studio_list_projects",
        "mcp__gmr__studio_get_project",
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
    got = _names(turn_tool_specs(GENERATED, True, ROUTES, has_studio=True))
    for dropped in ("get_series", "list_datasets", "contract_sectors",
                    "single_bidder_rate"):
        assert dropped not in got
    assert OFFERED_GENERATED == ("get_doc",)


def test_find_paths_is_no_longer_advertised():
    """It needs two resolved ids before it can be called at all, and was
    being chosen over search_entities for questions search answers."""
    assert "mcp__gmr__find_paths" not in OFFERED_BUILTINS
    assert "mcp__gmr__find_paths" not in _names(
        turn_tool_specs(GENERATED, True, ROUTES, has_studio=True))


def test_studio_tools_are_withheld_where_the_studio_is_not_available():
    without = _names(turn_tool_specs(GENERATED, True, ROUTES, has_studio=False))
    for name in studio_tools.STUDIO_ACTIONS:
        assert name not in without


def test_reading_comes_before_writing_in_the_array():
    """Order is a hint the model reads. list and get precede create, because
    adding to the project the user already has is almost always the intent."""
    got = _names(turn_tool_specs(GENERATED, True, ROUTES, has_studio=True))
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
class _FakeService:
    """Stands in for DataProjectService, recording the user it was called as."""

    def __init__(self):
        self.calls = []

    async def list_projects(self, user_id):
        self.calls.append(("list_projects", user_id))
        return [_P("p1", "Procurement", queries=[_Q("q1", "x" * 5000)])]

    async def get_project(self, user_id, project_id):
        self.calls.append(("get_project", user_id, project_id))
        return _P(project_id, "Procurement", queries=[_Q("q1", "y" * 5000)])

    async def create_project(self, user_id, name, investigation_id=None):
        self.calls.append(("create_project", user_id, name, investigation_id))
        return _P("new", name)

    async def add_query(self, user_id, project_id, name, lang, query):
        self.calls.append(("add_query", user_id, project_id, lang))
        return _Q("q2", query, name=name, lang=lang)


class _P:
    def __init__(self, pid, name, queries=None, plots=None):
        self.id, self.name = pid, name
        self.investigation_id = None
        self.queries = queries or []
        self.plots = plots or []


class _Q:
    def __init__(self, qid, query, name="Q", lang="cypher"):
        self.id, self.query, self.name, self.lang = qid, query, name, lang


@pytest.mark.asyncio
async def test_operations_run_as_the_asking_user():
    """The service checks access per call, so the agent inherits exactly the
    user's permissions — but only if the id is actually threaded through."""
    svc = _FakeService()
    ops = StudioOps(svc, "user-42")
    await ops.execute("mcp__gmr__studio_list_projects", {})
    await ops.execute("mcp__gmr__studio_create_project", {"name": "New"})
    assert all(call[1] == "user-42" for call in svc.calls)


@pytest.mark.asyncio
async def test_listing_abbreviates_query_text():
    """A project with ten 8000-character queries would otherwise spend a
    whole turn's budget on one call."""
    ops = StudioOps(_FakeService(), "u")
    out = json.loads(await ops.execute(
        "mcp__gmr__studio_get_project", {"project_id": "p1"}))
    text = out["queries"][0]["query"]
    assert len(text) < 600
    assert "truncated" in text


@pytest.mark.asyncio
async def test_one_query_can_be_read_in_full_by_id():
    ops = StudioOps(_FakeService(), "u")
    out = json.loads(await ops.execute(
        "mcp__gmr__studio_get_project", {"project_id": "p1", "query_id": "q1"}))
    assert len(out["queries"][0]["query"]) == 5000


@pytest.mark.asyncio
async def test_a_failure_returns_an_error_the_model_can_act_on():
    """A raised exception aborts the turn mid-stream. The model can fix a
    bad id if it is told; it can do nothing with a dropped connection."""
    class Boom:
        async def get_project(self, *_a, **_k):
            raise PermissionError("not your project")

    out = json.loads(await StudioOps(Boom(), "u").execute(
        "mcp__gmr__studio_get_project", {"project_id": "nope"}))
    assert "PermissionError" in out["error"]
    assert out["tool"] == "mcp__gmr__studio_get_project"


@pytest.mark.asyncio
async def test_an_unknown_tool_is_reported_not_raised():
    out = json.loads(await StudioOps(_FakeService(), "u").execute("nope", {}))
    assert "unknown studio tool" in out["error"]


def test_every_engine_routes_studio_calls_to_the_ops():
    """Not to the HTTP executor, which is anonymous and read-only."""
    for mod in ("langgraph_client", "pydantic_ai_client"):
        src = pathlib.Path(f"src/assistant/{mod}.py").read_text("utf-8")
        assert "studio.execute(" in src, f"{mod} does not use the ops"
        assert "studio_ops" in src, f"{mod} never receives them"
