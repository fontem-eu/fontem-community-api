"""Small models get the verbs a task needs, not the instrument panel.

The staging gate caught the regression this exists to prevent: the tool
surface grew past sixteen with the tooling rework, and the 1.7B stopped
selecting `navigate` — the repo had already measured that width breaking a
4B outright. The boundary reuses the schema tier: a model too small to
carry the schema in prefill is too small to choose among twenty tools.
"""
# pylint: disable=missing-function-docstring
from __future__ import annotations

from src.assistant import engine_tools, schema_context
from src.assistant.engine_tools import compact_for, turn_tool_specs

ROUTES = [{"path": "/map", "description": "Atlas"}]


def _names(specs):
    return [t["function"]["name"] for t in specs]


def test_the_compact_surface_is_at_most_the_width_that_worked():
    # ~12 tools navigated fine for months; sixteen broke a 4B. The compact
    # surface must stay at or under the proven width, editor or not.
    assert len(turn_tool_specs([], False, ROUTES, compact=True)) <= 12
    assert len(turn_tool_specs([], True, ROUTES, compact=True)) <= 14


def test_compact_keeps_every_loop_closable():
    names = _names(turn_tool_specs([], True, ROUTES, compact=True))
    # The discovery chain, the document loop, arithmetic, and the Studio
    # loop from create to RUN. Dropping a verb that closes a loop would
    # trade the navigation regression for a thrash regression.
    for needed in ("navigate", "mcp__gmr__search_entities",
                   "mcp__gmr__read_document", "mcp__gmr__replace_body",
                   "mcp__gmr__calculate", "mcp__gmr__studio_create_project",
                   "mcp__gmr__studio_add_query", "mcp__gmr__studio_run_query"):
        assert needed in names, needed


def test_full_surface_is_unchanged_by_default():
    assert "mcp__gmr__query_graph" in _names(turn_tool_specs([], False, ROUTES))
    assert "mcp__gmr__query_graph" not in _names(
        turn_tool_specs([], False, ROUTES, compact=True))


def test_the_boundary_is_the_schema_tier():
    assert compact_for({"local_model_id": "qwen3-1.7b"}) is True
    assert compact_for({"local_model_id": "qwen3-8b"}) is True
    assert compact_for({"local_model_id": "gpt-oss-120b"}) is False
    assert compact_for({"local_model_id": "ox-alpha"}) is False


def test_a_byok_credential_always_gets_the_full_surface():
    payload = {"local_model_id": "qwen3-1.7b",
               "credential": {"provider": "mistral", "api_key": "k"}}
    assert compact_for(payload) is False


def test_the_two_tiers_share_one_boundary():
    # If the schema threshold ever moves, the surface moves with it —
    # deliberately one decision, not two that can drift.
    assert engine_tools.compact_for(
        {"local_model_id": "qwen3-1.7b"}) == (
        32_768 < schema_context.SCHEMA_MIN_CONTEXT_TOKENS)
