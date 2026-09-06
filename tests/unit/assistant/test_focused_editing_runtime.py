"""The wiring between the model's coordinates and the editor's.

doc_edit is tested as pure functions elsewhere. What is pinned here is the
part that has to be right for a card to do anything: the offsets a model
gets from one tool are the offsets another consumes, and what the editor
needs rides back in the RESULT rather than in the arguments.
"""
# pylint: disable=protected-access
from __future__ import annotations

import asyncio
import json

from src.assistant import doc_edit
from src.assistant.tool_runtime import (
    _replace_part_result,
    _resolve_at_block,
    _with_at_block,
)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _p(text):
    return {"type": "paragraph", "content": [{"type": "text", "text": text}]}


DOC = {"type": "doc", "content": [_p("Alpha one."), _p("Beta two.")]}


class _Doc:
    """A DocOps stand-in: only `content` matters to these paths."""

    def __init__(self, content=None):
        self._content = DOC if content is None else content

    async def content(self):
        return self._content


class TestReplacePartCarriesTheRevisedDocument:

    def test_the_result_carries_the_whole_revised_body(self):
        out = json.loads(_replace_part_result(
            DOC, {"start": 0, "end": 5, "new_text": "Omega"}))
        assert out["proposed"] is True
        assert out["action"] == "replace_body", (
            "the editor applies a whole-body swap either way; the verb is "
            "what differs, not the apply path"
        )
        assert doc_edit.body_text(out["content_json"]) == "Omega one.\n\nBeta two."

    def test_it_carries_json_not_html(self):
        # A Studio plot in the article has data_params/ui_params objects
        # that do not survive an HTML round trip. Editing prose around a
        # chart must not delete the chart.
        doc = {"type": "doc", "content": [
            _p("Before."),
            {"type": "widget", "attrs": {"widget_type": "pipeline",
                                         "data_params": {"sources": [1]},
                                         "ui_params": {"kind": "bar"}}},
            _p("After.")]}
        out = json.loads(_replace_part_result(
            doc, {"start": 0, "end": 6, "new_text": "Later"}))
        widget = out["content_json"]["content"][1]
        assert widget["attrs"]["data_params"] == {"sources": [1]}, (
            "the chart's recipe must survive an edit to the prose beside it"
        )

    def test_the_new_body_text_comes_back_for_the_next_edit(self):
        out = json.loads(_replace_part_result(
            DOC, {"start": 0, "end": 5, "new_text": "Omega"}))
        assert out["body_text"] == "Omega one.\n\nBeta two.", (
            "offsets shift after every edit; handing back the new text saves "
            "a re-read before the next one"
        )

    def test_a_bad_span_is_refused_with_something_actionable(self):
        out = json.loads(_replace_part_result(
            DOC, {"start": 0, "end": 999, "new_text": "x"}))
        assert "error" in out and "21 characters" in out["error"]

    def test_non_integer_offsets_are_refused_by_name(self):
        out = json.loads(_replace_part_result(
            DOC, {"start": "the start", "end": 4, "new_text": "x"}))
        assert "error" in out and "find_in_document" in out["error"]


class TestAtCharBecomesAtBlock:

    def test_a_char_inside_the_first_block_places_after_it(self):
        got = _run(_resolve_at_block(
            _Doc(), "mcp__gmr__insert_studio_plot", {"at_char": 4}))
        assert got == 1

    def test_no_at_char_means_append(self):
        got = _run(_resolve_at_block(
            _Doc(), "mcp__gmr__insert_widget", {"widget_type": "x"}))
        assert got is None

    def test_a_nonsense_at_char_appends_rather_than_losing_the_widget(self):
        got = _run(_resolve_at_block(
            _Doc(), "mcp__gmr__insert_widget", {"at_char": "somewhere"}))
        assert got is None

    def test_tools_that_are_not_inserts_are_left_alone(self):
        got = _run(_resolve_at_block(
            _Doc(), "mcp__gmr__replace_body", {"at_char": 4}))
        assert got is None

    def test_no_open_document_means_append(self):
        got = _run(_resolve_at_block(
            None, "mcp__gmr__insert_widget", {"at_char": 4}))
        assert got is None


class TestAtBlockRidesInTheResultNotTheArguments:
    """The card is matched to its refusal by comparing the args the server
    saw with the args the card was drawn from. A position added to `args`
    would make every positioned widget fail that comparison."""

    def test_it_is_merged_into_a_successful_proposal(self):
        out = json.loads(_with_at_block(
            json.dumps({"proposed": True, "action": "insert_widget"}), 2))
        assert out["at_block"] == 2

    def test_a_refusal_is_left_exactly_as_it_was(self):
        raw = json.dumps({"error": "no such entity"})
        assert _with_at_block(raw, 2) == raw, (
            "a refused card must stay refused; decorating it with a position "
            "would suggest it is applicable"
        )

    def test_no_position_leaves_the_result_untouched(self):
        raw = json.dumps({"proposed": True})
        assert _with_at_block(raw, None) == raw

    def test_a_non_json_result_is_passed_through(self):
        assert _with_at_block("not json at all", 1) == "not json at all"
