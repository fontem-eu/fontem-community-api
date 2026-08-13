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
def test_the_executor_fills_the_name_cache_from_tool_results(mod):
    src = pathlib.Path(mod.__file__).read_text("utf-8")
    assert "_capture_names(" in src, (
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
