"""Things both executors must do, which only the deleted one did.

When the hand-written loop was decommissioned, pylint flagged two imports as
newly unused: `_capture_names` and `_language_directive`. Nothing had broken
— the loop was simply the only caller. Which means both behaviours had been
absent from the framework executors since the day they were written, and
production lost them silently when it switched to PydanticAI on 2026-08-12:

  * the tool status line showed raw UUIDs instead of entity names, because
    the name cache was created, passed to the formatter, and never filled;
  * the assistant answered in English regardless of the user's locale.

An unused import is a weak signal for a user-visible regression. These tests
are the strong one.
"""
import json
import pathlib

import pytest

from src.assistant import langgraph_client as lg
from src.assistant import pydantic_ai_client as pai
from src.assistant.entities import _capture_names
from src.assistant.language import _language_directive
from src.assistant.tool_runtime import _tool_detail

EXECUTORS = (pai, lg)
ENTITY_ID = "867f66f4-4aa4-5737-9bed-d51e2746a729"
INVESTIGATE_RESULT = {
    "label": "Company",
    "entity_id": ENTITY_ID,
    "props": {"gmr_id": ENTITY_ID, "company_name": "Siemens Energy AG/ADR"},
    "summary": "Siemens Energy AG/ADR is a Company (DE)",
}


@pytest.mark.parametrize("mod", EXECUTORS, ids=lambda m: m.__name__.split(".")[-1])
def test_the_executor_routes_tools_through_the_shared_dispatch(mod):
    # Which is what fills the name cache. Asserted here rather than by
    # grepping for _capture_names: the capture moved into ToolRuntime so both
    # engines get it from one place, and duplicating it back would be the
    # regression, not the fix.
    src = pathlib.Path(mod.__file__).read_text("utf-8")
    assert "dispatch(" in src
    assert "name_cache" in src


def test_the_shared_dispatch_is_what_fills_the_cache():
    runtime = pathlib.Path(
        "src/assistant/tool_runtime.py").read_text("utf-8")
    assert "_capture_names(" in runtime, (
        "the name cache is passed to the status formatter; if nothing fills "
        "it the panel shows UUIDs"
    )


@pytest.mark.parametrize("mod", EXECUTORS, ids=lambda m: m.__name__.split(".")[-1])
def test_the_executor_applies_the_language_directive(mod):
    src = pathlib.Path(mod.__file__).read_text("utf-8")
    assert "_language_directive(" in src
    assert "locale" in src


@pytest.mark.parametrize("mod", EXECUTORS, ids=lambda m: m.__name__.split(".")[-1])
def test_the_name_cache_is_shared_not_rebuilt_per_call(mod):
    # A fresh dict per tool call would fill correctly and still show UUIDs,
    # because the status events read a different one.
    src = pathlib.Path(mod.__file__).read_text("utf-8")
    assert "name_cache" in src
    assert 'name_cache if name_cache is not None else {}' in src, (
        "the run loop must read the dict the closures filled"
    )


def test_capture_names_reads_an_investigate_payload():
    # The shape the executors actually hand it. props carry `company_name`,
    # not `name` — reading only `name` is why an investigated entity kept
    # rendering as a UUID.
    cache: dict = {}
    _capture_names(cache, INVESTIGATE_RESULT)
    assert cache.get(ENTITY_ID) == "Siemens Energy AG/ADR"


def test_capture_names_reads_an_authority_payload():
    cache: dict = {}
    _capture_names(cache, {"props": {
        "authority_id": "auth-1", "authority_name": "Metro Mondego, S. A.",
    }})
    assert cache.get("auth-1") == "Metro Mondego, S. A."


def test_capture_names_reads_a_search_payload():
    # The shape that always worked: search results use a plain `name`.
    cache: dict = {}
    _capture_names(cache, {"companies": [{"gmr_id": "c-1", "name": "ACME"}],
                           "authorities": [{"authority_id": "a-1", "name": "DG HOME"}]})
    assert cache == {"c-1": "ACME", "a-1": "DG HOME"}


def test_capture_names_ignores_an_entity_with_no_name_at_all():
    # The 200-skeleton again: an id echoed back with every field null must
    # not be remembered as an entity.
    cache: dict = {}
    _capture_names(cache, {"props": {"gmr_id": "ghost", "company_name": None}})
    assert not cache


def test_a_filled_cache_turns_a_uuid_into_a_name_in_the_status_line():
    cache: dict = {}
    _capture_names(cache, json.loads(json.dumps(INVESTIGATE_RESULT)))
    detail = _tool_detail(
        "mcp__gmr__investigate_entity", {"entity_id": ENTITY_ID}, cache,
    )
    assert "Siemens Energy AG/ADR" in detail
    assert ENTITY_ID not in detail


def test_the_language_directive_is_a_no_op_without_a_locale():
    # Every turn calls this; it must not append noise when there is nothing
    # to say.
    assert not _language_directive(None)


def test_the_language_directive_names_the_language_when_there_is_one():
    out = _language_directive("pt")
    assert out, "a locale the platform ships must produce a directive"


# ── The shared dispatch, exercised directly ────────────────────
#
# Both engines now route every tool call through ToolRuntime.dispatch, so
# it is worth driving rather than only grepping for.


def _dispatch(name, args, *, studio=None, routes=None, raw='{"ok": true}'):
    """Run one dispatch against a runtime whose execute_tool is stubbed."""
    from src.assistant.tool_runtime import ToolRuntime  # pylint: disable=import-outside-toplevel
    runtime = ToolRuntime(gmr_api_url="http://fake")

    async def _fake_execute(_client, _name, _args):
        return raw
    runtime.execute_tool = _fake_execute  # type: ignore[method-assign]

    pending, cache, budget = [], {}, [10_000]
    result, raw_len = _run(runtime.dispatch(
        None, name, args, studio=studio, nav_routes=routes or ROUTES,
        pending_nav=pending, budget=budget, name_cache=cache,
    ))
    return result, raw_len, pending, cache


def _run(coro):
    import asyncio  # pylint: disable=import-outside-toplevel
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


ROUTES = [{"path": "/map", "description": "Atlas"}]


def test_dispatch_fills_the_name_cache_from_a_real_result():
    _, _, _, cache = _dispatch(
        "mcp__gmr__investigate_entity", {"entity_id": ENTITY_ID},
        raw=json.dumps(INVESTIGATE_RESULT),
    )
    assert cache.get(ENTITY_ID) == "Siemens Energy AG/ADR"


def test_dispatch_queues_a_navigation_and_reports_no_raw_length():
    result, raw_len, pending, _ = _dispatch("navigate", {"path": "/map"})
    assert pending == [{"path": "/map"}]
    assert '"ok": true' in result.lower()
    # 0 means "answered locally" — the trace bubble is for fontem-api calls.
    assert raw_len == 0


def test_dispatch_refuses_a_studio_call_with_no_ops_rather_than_crashing():
    result, raw_len, _, _ = _dispatch(
        "mcp__gmr__studio_list_projects", {}, studio=None)
    assert "not available" in result
    assert raw_len == 0


def test_dispatch_reports_the_raw_length_so_a_trace_can_be_emitted():
    body = json.dumps({"companies": [{"gmr_id": "c-1", "name": "ACME"}]})
    _, raw_len, _, cache = _dispatch("mcp__gmr__search_entities", {"q": "a"},
                                     raw=body)
    assert raw_len == len(body)
    assert cache == {"c-1": "ACME"}


def test_dispatch_survives_a_tool_result_that_is_not_json():
    # Legacy tools and error paths can return prose; the cache update must
    # not take the turn down with it.
    result, raw_len, _, cache = _dispatch(
        "mcp__gmr__search_entities", {"q": "a"}, raw="not json at all")
    assert result == "not json at all"
    assert raw_len == len("not json at all")
    assert not cache
