"""The second executor, and the properties that make it comparable.

A second engine is only useful if it is swappable. If the two disagree about
which tools the model is offered, or emit different SSE events, then running
the battery against each measures the difference between the harnesses rather
than the difference between the loops — which is the whole reason for having
two.

These tests do not exercise LangGraph itself (that is its own test suite).
They pin the seams: the flag, the tool surface, the failure mode when the
dependency is absent, and the SSE vocabulary.
"""
import pytest

from src.assistant import langgraph_client as lg
from src.assistant import tool_budget
from src.assistant import navigation
from src.assistant.mistral_client import _turn_tools


def test_engine_is_off_unless_explicitly_selected(monkeypatch):
    """The native loop stays the default until the two have been compared."""
    monkeypatch.delenv(lg.ENGINE_ENV, raising=False)
    assert lg.engine_selected() is False
    monkeypatch.setenv(lg.ENGINE_ENV, "native")
    assert lg.engine_selected() is False
    monkeypatch.setenv(lg.ENGINE_ENV, "langgraph")
    assert lg.engine_selected() is True
    monkeypatch.setenv(lg.ENGINE_ENV, "  LangGraph  ")
    assert lg.engine_selected() is True, "flag should tolerate case and spaces"


def _names(specs):
    return [s["function"]["name"] for s in specs]


def test_tool_surface_matches_the_native_engine_exactly():
    """Same tools, same order, or a comparison measures the wrong thing.

    Order is asserted, not just membership: navigate leads the array
    deliberately. With it appended last the 4B stopped calling it at all —
    no error, just silence — so position is behaviour here, not style.
    """
    routes = [{"path": "/map", "description": "Atlas"}]
    for has_editor in (True, False):
        native = _names(_turn_tools(routes, has_editor))
        mine = _names(lg.turn_tool_specs([], has_editor, routes))
        assert mine == native, f"surface drifted at has_editor={has_editor}"


def test_propose_edit_is_scoped_out_without_an_editor():
    """The scoping rule that cost a day to find must hold in both engines."""
    routes = [{"path": "/map", "description": "Atlas"}]
    with_editor = _names(lg.turn_tool_specs([], True, routes))
    without = _names(lg.turn_tool_specs([], False, routes))
    assert "mcp__gmr__propose_edit" in with_editor
    assert "mcp__gmr__propose_edit" not in without


def test_navigate_is_offered_only_when_the_client_sent_a_site_map():
    """A navigate tool with no routes can only produce dead links."""
    assert navigation.NAVIGATE_TOOL_NAME in _names(
        lg.turn_tool_specs([], True, [{"path": "/map", "description": "Atlas"}]))
    assert navigation.NAVIGATE_TOOL_NAME not in _names(
        lg.turn_tool_specs([], True, []))


def test_the_shared_tool_budget_is_the_same_object():
    """Not a re-derived constant.

    A different cap in each engine would make them incomparable on the same
    fixture, and would reintroduce the overflow this budget exists to stop.
    """
    assert lg.tool_budget is tool_budget


@pytest.mark.asyncio
async def test_missing_message_fails_the_same_way_as_the_native_engine():
    client = lg.LangGraphProxyClient(local_url="http://llm")
    events = [e async for e in client.stream({})]
    assert any('"error"' in e and "Missing message" in e for e in events)


@pytest.mark.asyncio
async def test_absent_local_url_is_reported_not_crashed():
    """Fails the turn legibly rather than raising into the SSE stream."""
    client = lg.LangGraphProxyClient(local_url="")
    events = [e async for e in client.stream({"message": "hi"})]
    body = "".join(events)
    assert "event: error" in body
    assert "LOCAL_LLM_URL" in body
    assert body.rstrip().endswith("event: done\ndata: {}"), \
        "the stream must terminate even when the turn fails"


@pytest.mark.asyncio
async def test_a_missing_dependency_degrades_to_an_error_event(monkeypatch):
    """An uninstalled extra must not take the service down.

    The import is deferred precisely so this surfaces on the turn that
    needed it, as a message the panel can render, rather than as an
    ImportError at app start.
    """
    def boom():
        raise lg.LangGraphUnavailable("No module named 'langchain'")
    monkeypatch.setattr(lg, "_import_langchain", boom)
    client = lg.LangGraphProxyClient(local_url="http://llm")
    body = "".join([e async for e in client.stream({"message": "hi"})])
    assert "event: error" in body
    assert "langgraph engine unavailable" in body
    assert "event: done" in body


def test_di_selects_the_engine_from_the_environment(monkeypatch):
    """The switch is real, not just a function nobody calls."""
    # pylint: disable=import-outside-toplevel
    from src.api.di import AssistantProvider
    monkeypatch.setenv("LLM_PROVIDER", "mistral")
    monkeypatch.setenv("LOCAL_LLM_URL", "http://llm")

    monkeypatch.setenv(lg.ENGINE_ENV, "langgraph")
    assert isinstance(AssistantProvider().proxy_client(), lg.LangGraphProxyClient)

    monkeypatch.delenv(lg.ENGINE_ENV)
    assert not isinstance(AssistantProvider().proxy_client(), lg.LangGraphProxyClient)
