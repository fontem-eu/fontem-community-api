"""The assistant can put a Studio chart into the article.

The gap this closes, from a production session on 2026-08-31: the model
was asked to chart EU spending with Israeli companies, built the plot in
the Studio — and then had no verb that could embed it. It described the
chart in prose instead. Studio and the editor were two halves of a
product with no bridge between them.

The bridge is deliberately a *recipe*, not a picture: the same
`pipeline` widget the Studio's own Pocket button produces, whose queries
and transform re-run when a reader opens the article.
"""
# pylint: disable=protected-access
from __future__ import annotations

import asyncio
import json
import types

import pytest

from src.assistant import doc_tools, engine_tools, navigation
from src.assistant.tool_runtime import ToolRuntime


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class _Plot:
    def __init__(self, pid, name, spec):
        self.id, self.name, self.spec = pid, name, spec


class _Project:
    def __init__(self, plots):
        self.plots = plots


class _Svc:
    """Stands in for data_project_service, scoped to one user."""

    def __init__(self, project=None, raises=None):
        self._project, self._raises = project, raises

    async def get_project(self, _user, _project_id):
        if self._raises:
            raise self._raises
        return self._project


GOOD_SPEC = {
    "chart": "bar_h", "x": "country", "y": "total_eur",
    "sources": [{"name": "spend", "lang": "sql", "query": "select 1"}],
    "transform": "SELECT * FROM spend",
    "series": [], "corrCols": [],
}


def _studio(svc):
    from src.assistant.studio_ops import StudioOps
    return StudioOps(svc, "user-1")


class TestTheRecipeAStudioPlotBecomes:

    def test_a_saved_plot_becomes_the_widget_the_editor_embeds(self):
        studio = _studio(_Svc(_Project([_Plot("p1", "Spending by country", GOOD_SPEC)])))
        out = _run(studio.plot_recipe("proj", "p1"))
        assert out["name"] == "Spending by country"
        # data_params carries the recipe, never rows: the chart re-runs.
        assert out["data_params"]["sources"][0]["name"] == "spend"
        assert out["data_params"]["transform"] == "SELECT * FROM spend"
        assert out["ui_params"]["chart"] == "bar_h"
        assert out["ui_params"]["x"] == "country"
        assert out["ui_params"]["y"] == "total_eur"
        assert "rows" not in json.dumps(out)

    def test_a_plot_with_no_chart_is_refused_with_a_reason(self):
        spec = dict(GOOD_SPEC); spec.pop("chart")
        studio = _studio(_Svc(_Project([_Plot("p1", "half-built", spec)])))
        out = _run(studio.plot_recipe("proj", "p1"))
        assert "error" in out and "chart" in out["error"]
        assert out["hint"]

    def test_a_plot_with_no_sources_cannot_re_run_so_is_refused(self):
        spec = dict(GOOD_SPEC); spec["sources"] = []
        studio = _studio(_Svc(_Project([_Plot("p1", "sourceless", spec)])))
        out = _run(studio.plot_recipe("proj", "p1"))
        assert "error" in out and "source" in out["error"]

    def test_a_plot_id_that_is_not_in_the_project_resolves_to_nothing(self):
        studio = _studio(_Svc(_Project([_Plot("p1", "mine", GOOD_SPEC)])))
        out = _run(studio.plot_recipe("proj", "someone-elses"))
        assert "error" in out


class TestTheProposalTheModelGets:

    @staticmethod
    def _runtime():
        return ToolRuntime.__new__(ToolRuntime)

    def test_a_valid_plot_is_proposed_with_the_recipe_attached(self):
        studio = _studio(_Svc(_Project([_Plot("p1", "Spending", GOOD_SPEC)])))
        out = json.loads(_run(self._runtime()._validate_studio_plot(
            studio, {"project_id": "proj", "plot_id": "p1"})))
        assert out["proposed"] is True
        assert out["action"] == "insert_studio_plot"
        assert out["plot_name"] == "Spending"
        # The card carries what the editor needs, so applying it does not
        # depend on a second fetch that could disagree with what was validated.
        assert out["data_params"]["sources"]
        assert out["ui_params"]["chart"] == "bar_h"

    def test_missing_ids_are_refused_before_any_lookup(self):
        out = json.loads(_run(self._runtime()._validate_studio_plot(
            _studio(_Svc(_Project([]))), {"project_id": "", "plot_id": ""})))
        assert "required" in out["error"]
        assert "proposed" not in out

    def test_a_project_that_raises_reads_as_a_bad_id_not_a_platform_fault(self):
        studio = _studio(_Svc(raises=KeyError("no such project")))
        out = json.loads(_run(self._runtime()._validate_studio_plot(
            studio, {"project_id": "nope", "plot_id": "p1"})))
        assert "cannot read plot" in out["error"]
        assert "studio_list_projects" in out["hint"]
        assert "proposed" not in out

    def test_no_studio_on_the_turn_is_said_plainly(self):
        out = json.loads(_run(self._runtime()._validate_studio_plot(
            None, {"project_id": "p", "plot_id": "q"})))
        assert "not available" in out["error"]


class TestTheToolSurface:

    def test_the_verb_is_offered_and_maps_to_a_frontend_action(self):
        assert "mcp__gmr__insert_studio_plot" in engine_tools.OFFERED_BUILTINS
        assert doc_tools.PROPOSAL_TOOL_ACTIONS["mcp__gmr__insert_studio_plot"] \
            == "insert_studio_plot"

    def test_it_is_withdrawn_when_there_is_nothing_to_insert_into(self):
        # Same rule as every other document verb: no editor, no offer.
        assert "mcp__gmr__insert_studio_plot" in navigation.EDITOR_ONLY_TOOLS
        spec = next(t for t in doc_tools.DOC_TOOLS
                    if t["function"]["name"] == "mcp__gmr__insert_studio_plot")
        assert navigation.scope_tools([spec], has_editor=False) == []
        assert navigation.scope_tools([spec], has_editor=True) == [spec]

    def test_it_takes_ids_only_and_both_are_required(self):
        # The lesson from propose_edit: required params only, no flags whose
        # validity depends on another field.
        spec = next(t for t in doc_tools.DOC_TOOLS
                    if t["function"]["name"] == "mcp__gmr__insert_studio_plot")
        params = spec["function"]["parameters"]
        assert set(params["properties"]) == {"project_id", "plot_id"}
        assert set(params["required"]) == {"project_id", "plot_id"}

    def test_a_studio_plot_is_not_smuggled_into_the_entity_widget_enum(self):
        # insert_widget requires an entityId; a plot has none.
        assert "studio_plot" not in doc_tools.WIDGET_TYPES
        assert "pipeline" not in doc_tools.WIDGET_TYPES
