"""The third executor, and the properties that keep three engines comparable.

Same contract as test_langgraph_engine.py, deliberately: if the engines
disagree about which tools the model is offered or which SSE events reach the
panel, running the same battery against each measures the harness rather than
the loop — which is the only reason to have three.

These do not exercise PydanticAI itself. They pin the seams: the flag, the
tool surface, the model-name resolution that took prod down once already, the
credential fallback, and the failure modes.
"""
import pathlib

import pytest

from src.assistant import local_models, tool_budget
from src.assistant import pydantic_ai_client as pai
from src.assistant.engine_tools import turn_tool_specs
from src.assistant.mistral_client import _turn_tools

ROUTES = [{"path": "/map", "description": "Atlas"}]


def test_engine_is_off_unless_explicitly_selected(monkeypatch):
    monkeypatch.delenv(pai.ENGINE_ENV, raising=False)
    assert pai.engine_selected() is False
    monkeypatch.setenv(pai.ENGINE_ENV, "langgraph")
    assert pai.engine_selected() is False, "must not answer to another engine's name"
    monkeypatch.setenv(pai.ENGINE_ENV, "pydantic-ai")
    assert pai.engine_selected() is True
    monkeypatch.setenv(pai.ENGINE_ENV, "  Pydantic-AI  ")
    assert pai.engine_selected() is True


def test_the_three_engines_offer_an_identical_tool_surface():
    """Same tools, same order, or the comparison is worthless.

    Order is asserted, not just membership: navigate leads the array
    deliberately, and appended last the 4B stopped calling it at all.
    """
    for has_editor in (True, False):
        native = [s["function"]["name"] for s in _turn_tools(ROUTES, has_editor)]
        shared = [s["function"]["name"] for s in turn_tool_specs([], has_editor, ROUTES)]
        assert shared == native, f"surface drifted at has_editor={has_editor}"


def test_the_model_id_is_resolved_to_the_served_name():
    """The bug that took production down when LangGraph was switched on.

    The prod agent runs in router mode and serves "qwen3-4b-q4_k_m";
    LOCAL_LLM_MODEL holds the id. Passing the id through is a 400 on every
    single turn — the flag right, the env right, and nothing working.
    """
    assert local_models.resolve("qwen3-4b").served_name == "qwen3-4b-q4_k_m"
    src = pathlib.Path(pai.__file__).read_text("utf-8")
    assert "local_models.resolve(" in src
    assert "served_name" in src


def test_the_tool_budget_is_the_shared_one():
    """A different cap here would make the engines incomparable, and would
    reintroduce the context overflow the budget exists to stop."""
    assert pai.tool_budget is tool_budget


@pytest.mark.asyncio
async def test_missing_message_fails_like_the_other_engines():
    client = pai.PydanticAIProxyClient(local_url="http://llm")
    body = "".join([e async for e in client.stream({})])
    assert "event: error" in body and "Missing message" in body


@pytest.mark.asyncio
async def test_absent_local_url_is_reported_and_the_stream_still_ends():
    client = pai.PydanticAIProxyClient(local_url="")
    body = "".join([e async for e in client.stream({"message": "hi"})])
    assert "event: error" in body
    assert "LOCAL_LLM_URL" in body
    assert body.rstrip().endswith("event: done\ndata: {}")


@pytest.mark.asyncio
async def test_a_missing_dependency_degrades_to_an_error_event(monkeypatch):
    def boom():
        raise pai.PydanticAIUnavailable("No module named 'pydantic_ai'")
    monkeypatch.setattr(pai, "_import_pydantic_ai", boom)
    client = pai.PydanticAIProxyClient(local_url="http://llm")
    body = "".join([e async for e in client.stream({"message": "hi"})])
    assert "pydantic-ai engine unavailable" in body
    assert "event: done" in body


@pytest.mark.asyncio
async def test_a_byok_turn_is_handed_back_to_the_native_client():
    """Never silently downgrade someone spending their own key."""
    client = pai.PydanticAIProxyClient(local_url="http://llm")
    seen = []

    async def fake_native_stream(payload):
        seen.append(payload)
        yield 'event: chunk\ndata: {"text":"from native"}\n\n'

    # pylint: disable=protected-access
    client._native.stream = fake_native_stream
    body = "".join([e async for e in client.stream({
        "message": "hi", "credential": ("mistral", "sk-user", "magistral-medium-latest"),
    })])
    assert "from native" in body
    assert len(seen) == 1


def test_a_propose_edit_call_carries_the_proposal_the_card_renders():
    """The Apply card is built from status.proposal; without it the tool
    fires and the user sees nothing to accept."""
    class Part:
        tool_name = "mcp__gmr__propose_edit"
        args = '{"action": "insert_content", "content": "<p>x</p>"}'

    class Ev:
        part = Part()

    out = pai.PydanticAIProxyClient._tool_status(Ev(), {}, 0.0)  # pylint: disable=protected-access
    assert '"proposal"' in out
    assert "insert_content" in out
    assert '"phase":"tool_use"' in out


def test_malformed_tool_args_do_not_take_the_turn_down():
    """A half-streamed or invalid arg blob must degrade, not raise."""
    class Part:
        tool_name = "mcp__gmr__search_entities"
        args = '{"query": "unterminated'

    class Ev:
        part = Part()

    out = pai.PydanticAIProxyClient._tool_status(Ev(), {}, 0.0)  # pylint: disable=protected-access
    assert "event: status" in out
    assert "search_entities" in out
