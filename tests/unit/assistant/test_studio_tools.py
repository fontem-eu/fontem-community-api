"""Data Studio actions the model can propose.

The Studio is where an analysis is built: a project holds source queries
(Cypher/SQL/SPARQL) and plots that combine them through DuckDB in the
browser. The assistant could describe all of it and touch none of it.

These are proposals rather than writes, for the same reason propose_edit is:
the tool executor talks to fontem-api over plain GET with no user identity,
so a server-side create would act as nobody. The browser performs them with
the session it already has.
"""
import json
import pathlib

from src.assistant import studio_tools
from src.assistant.engine_tools import turn_tool_specs
from src.assistant.mistral_client import _turn_tools

ROUTES = [{"path": "/map", "description": "Atlas"}]


def _names(specs):
    return [s["function"]["name"] for s in specs]


def test_the_three_actions_a_full_analysis_needs():
    """Create a project, add a source, add a plot. Anything less leaves the
    model able to start something it cannot finish."""
    assert _names(studio_tools.STUDIO_TOOLS) == [
        "mcp__gmr__studio_create_project",
        "mcp__gmr__studio_add_query",
        "mcp__gmr__studio_add_plot",
    ]


def test_studio_tools_are_withheld_where_they_cannot_be_executed():
    """Same rule as propose_edit: a proposal nothing can apply is worse than
    no tool, because the model cannot tell why it failed."""
    with_studio = _names(turn_tool_specs([], True, ROUTES, has_studio=True))
    without = _names(turn_tool_specs([], True, ROUTES, has_studio=False))
    for name in studio_tools.STUDIO_ACTIONS:
        assert name in with_studio
        assert name not in without


def test_all_three_engines_offer_the_same_studio_surface():
    """The native loop builds its own tool list; the framework engines share
    engine_tools. They must not drift, or a comparison measures the tool
    list instead of the loop."""
    native = _names(_turn_tools(ROUTES, True, True))
    shared = _names(turn_tool_specs([], True, ROUTES, has_studio=True))
    assert native == shared


def test_the_query_languages_are_pinned_to_what_the_studio_runs():
    """The model cannot guess these, and a wrong `lang` is a 400 it has no
    way to diagnose from the error."""
    assert studio_tools.QUERY_LANGS == ("cypher", "sql", "sparql")
    schema = studio_tools.STUDIO_TOOLS[1]["function"]["parameters"]
    assert schema["properties"]["lang"]["enum"] == list(studio_tools.QUERY_LANGS)


def test_the_query_tool_says_which_store_each_language_reaches():
    """Naming the engines is not enough: the model has to pick one from the
    question, and "which store holds contracts" is the actual decision."""
    desc = studio_tools.STUDIO_TOOLS[1]["function"]["description"]
    assert "graph" in desc and "cypher" in desc
    assert "sparql" in desc
    assert "sql" in desc


def test_the_plot_tool_explains_that_the_transform_is_duckdb():
    """The transform runs in the browser over the source results, whatever
    language produced them. A model that writes Cypher there gets a syntax
    error from a database it never chose."""
    desc = studio_tools.STUDIO_TOOLS[2]["function"]["description"]
    assert "DuckDB" in desc
    spec = studio_tools.STUDIO_TOOLS[2]["function"]["parameters"]["properties"]["spec"]
    for key in ("sources", "transform", "chart", "x", "y"):
        assert key in spec["properties"], f"spec.{key} undocumented"


def test_chart_types_match_what_the_renderer_understands():
    spec = studio_tools.STUDIO_TOOLS[2]["function"]["parameters"]["properties"]["spec"]
    assert spec["properties"]["chart"]["enum"] == list(studio_tools.CHART_TYPES)


def test_the_action_names_the_panel_must_recognise_are_exported():
    """The browser executes these. If the two lists drift, the model calls a
    tool and nothing happens — silently."""
    assert set(studio_tools.STUDIO_ACTIONS) == {
        t["function"]["name"] for t in studio_tools.STUDIO_TOOLS}


def test_every_engine_forwards_the_action_for_the_browser_to_run():
    """A studio tool that fires without a studio_action on the status event
    is a no-op the user never sees."""
    for mod in ("mistral_client", "langgraph_client", "pydantic_ai_client"):
        src = pathlib.Path(f"src/assistant/{mod}.py").read_text("utf-8")
        assert "studio_action" in src, f"{mod} does not forward the action"
        assert "STUDIO_ACTIONS" in src, f"{mod} does not recognise them"


def test_the_schemas_are_json_serialisable():
    """They ride to the model as JSON; a non-serialisable default would fail
    at request time rather than here."""
    assert json.loads(json.dumps(studio_tools.STUDIO_TOOLS))
