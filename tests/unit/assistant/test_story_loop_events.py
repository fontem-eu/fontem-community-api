"""The iteration harness reads the event stream correctly.

evals/story_loop.py is not a gate and does not run in CI — it talks to a
live environment. Its event PARSER is a pure function, though, and it has
already been wrong once in the way that matters: it keyed on a `tool_use`
event, which does not exist, recorded every call with empty arguments, and
still produced a report that looked complete. A harness you cannot trust
is worse than no harness, because you act on it.

So the shapes are pinned here, against what the server actually emits:
`tool_trace.trace()` for tool_result, `_tool_status` in
pydantic_ai_client for the status announcement.
"""
# pylint: disable=protected-access
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "evals"))

story_loop = pytest.importorskip(
    "story_loop", reason="evals/ not present in this checkout")


@pytest.fixture(name="loop")
def _loop():
    obj = story_loop.Loop.__new__(story_loop.Loop)
    obj.calls, obj.proposals, obj.reply, obj.errors, obj.usage = [], [], [], [], {}
    return obj


def test_tool_arguments_come_off_the_result_event(loop):
    """The bug this file exists for.

    There is no `tool_use` EVENT. A call announces itself as `status` with
    `phase: "tool_use"`, and the arguments arrive again on `tool_result`.
    Reading them off the result is what makes the trace complete.
    """
    loop._event("tool_result", json.dumps({
        "call_id": "abc", "tool": "mcp__gmr__replace_part",
        "args": {"start": 0, "end": 5, "new_text": "Omega"},
        "result": '{"proposed": true}', "bytes": 42, "elapsed": 1.2}))
    assert loop.calls[0]["args"] == {"start": 0, "end": 5, "new_text": "Omega"}, (
        "a trace with empty arguments hides half of what the model did, and "
        "the report still reads as complete"
    )
    assert loop.calls[0]["elapsed"] == 1.2


def test_a_proposal_is_captured_from_the_status_announcement(loop):
    loop._event("status", json.dumps({
        "phase": "tool_use", "tool": "mcp__gmr__replace_part",
        "proposal": {"start": 0, "end": 5, "action": "replace_body"}}))
    assert loop.proposals[0]["action"] == "replace_body"


def test_status_phases_that_are_not_calls_are_not_counted_as_calls(loop):
    for phase in ("connecting", "streaming"):
        loop._event("status", json.dumps({"phase": phase, "detail": "..."}))
    assert loop.calls == [] and loop.proposals == []


def test_a_truncated_result_is_surfaced_rather_than_swallowed(loop):
    """The model saw less than the tool produced. That explains an answer
    that looks like it ignored the data, so it must reach the report."""
    loop._event("status", json.dumps({"phase": "truncated", "tool": "x"}))
    assert loop.errors and "truncated" in loop.errors[0]


def test_the_reply_is_assembled_from_chunks_in_order(loop):
    loop._event("chunk", json.dumps({"text": "Here is the story. "}))
    loop._event("chunk", json.dumps({"text": "It has two plots."}))
    assert "".join(loop.reply) == "Here is the story. It has two plots."


def test_an_error_event_is_recorded(loop):
    loop._event("error", json.dumps({"detail": "upstream refused"}))
    assert loop.errors and "upstream refused" in loop.errors[0]


def test_a_malformed_payload_does_not_kill_the_run(loop):
    """A live stream can hand back anything. Losing the whole trace to one
    bad frame would waste the run that produced it."""
    loop._event("chunk", "not json at all")
    loop._event("tool_result", "{{{")
    assert loop.calls == [] and loop.reply == []


class TestReadingTheFinishedArticle:
    """The harness reports what the run produced, so it must read it.

    The field on GET /data-stories/{id} is `content_doc`, holding the
    STORED shape {"tiptap": doc, "version": n}. Reading a `content_json`
    that does not exist on that response returned None: every apply
    started from an empty document, and every finished article was
    reported as "0 blocks" while fifteen sat in the database. A report
    that under-counts the artifacts reads as the model having done
    nothing, which is the opposite of the truth and the worst way for a
    harness to be wrong.
    """

    def test_it_unwraps_the_stored_shape(self):
        doc = story_loop.article_doc(
            {"content_doc": {"version": 2, "tiptap": {
                "type": "doc", "content": [{"type": "paragraph"}]}}})
        assert doc["content"] == [{"type": "paragraph"}]

    def test_it_accepts_a_bare_document(self):
        doc = story_loop.article_doc(
            {"content_doc": {"type": "doc", "content": [{"type": "heading"}]}})
        assert doc["content"] == [{"type": "heading"}]

    def test_an_article_with_nothing_saved_is_empty_not_an_error(self):
        assert story_loop.article_doc({}) == {}
        assert story_loop.article_doc({"content_doc": None}) == {}
