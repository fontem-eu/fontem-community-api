"""The tool_result event, which exists so a turn can be read after the fact.

The stream has always said *that* a tool ran. What it returned was invisible,
so when the assistant said something odd there was no way to tell a bad tool
result from a bad reading of a good one. This event carries the other half.
"""
import json

from src.assistant import tool_trace


def _payload(sse: str) -> dict:
    return json.loads(sse.split("data: ", 1)[1])


def test_it_reports_what_the_model_saw_not_the_raw_response():
    """The truncated view is the one that explains the answer.

    If the model answered from 8k of a 90k result, showing the 90k explains
    its behaviour less well, not more — the gap is the finding.
    """
    t = tool_trace.trace("mcp__gmr__search_entities", {"query": "x"},
                         "seen-by-model", 1.0, raw_len=90_000)
    assert t["result"] == "seen-by-model"
    assert t["bytes"] == 90_000
    assert t["truncated"] is True


def test_an_untruncated_result_says_so():
    t = tool_trace.trace("t", {}, "all of it", 0.5)
    assert t["truncated"] is False
    assert t["bytes"] == len("all of it")


def test_a_huge_result_is_bounded_for_the_browser():
    """A debugging view that ships a megabyte per call stops being usable on
    exactly the results worth inspecting."""
    t = tool_trace.trace("t", {}, "z" * 500_000, 0.1)
    assert len(t["result"]) < tool_trace.MAX_DISPLAY_CHARS * 1.1
    assert "not shown here" in t["result"]


def test_the_display_cap_is_well_above_what_a_result_can_feed_the_model():
    """So the common case arrives whole."""
    # pylint: disable=import-outside-toplevel
    from src.assistant import tool_budget
    assert tool_trace.MAX_DISPLAY_CHARS > tool_budget.MAX_TOOL_RESULT_CHARS


def test_arguments_ride_along_so_a_call_can_be_reproduced():
    t = tool_trace.trace("mcp__gmr__investigate_entity",
                         {"entity_id": "gmr-1", "depth": 2}, "{}", 0.2)
    assert t["args"] == {"entity_id": "gmr-1", "depth": 2}


def test_non_dict_arguments_degrade_rather_than_raise():
    assert tool_trace.trace("t", None, "x", 0.1)["args"] == {}
    assert tool_trace.trace("t", "not-a-dict", "x", 0.1)["args"] == {}


def test_a_non_string_result_is_serialised_not_dropped():
    t = tool_trace.trace("t", {}, {"rows": [1, 2]}, 0.1)
    assert "rows" in t["result"]


def test_every_engine_emits_the_same_event_name():
    """Three engines, one contract. A panel that has to know which engine ran
    the turn is a panel that will get it wrong."""
    # pylint: disable=import-outside-toplevel
    import pathlib
    from src.assistant import (
        langgraph_client, mistral_client, pydantic_ai_client,
    )
    for mod in (mistral_client, langgraph_client, pydantic_ai_client):
        src = pathlib.Path(mod.__file__).read_text("utf-8")
        assert "tool_trace.EVENT" in src, f"{mod.__name__} does not emit it"
        assert "tool_trace.trace(" in src, f"{mod.__name__} builds no payload"
