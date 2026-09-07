"""Two small refusals that cost a run more than they saved.

Both from the story-loop run of 2026-09-07, and both the same shape: a
tool answered something true but not the thing the model needed to know,
and the model spent calls guessing.
"""
# pylint: disable=missing-function-docstring
from __future__ import annotations

import ast
import asyncio
import json

from src.assistant.calc_tools import execute, _NODE_HANDLERS


def test_a_parenthesised_result_is_the_same_request_as_a_bracketed_one():
    # `result = (a, b)` and `result = [a, b]` are the same request and must
    # get the same answer. The tuple form used to die at the parser with
    # "unsupported syntax: Tuple" while the list form got a different
    # message entirely. Both now simply work; what is pinned here is that
    # they agree, which is the property that was broken.
    tup = json.loads(execute({"expression": "result = (1, 2)"}))
    lst = json.loads(execute({"expression": "result = [1, 2]"}))
    assert tup["result"] == lst["result"] == [1, 2]


def test_tuple_is_whitelisted_alongside_list():
    assert _NODE_HANDLERS[ast.Tuple] is _NODE_HANDLERS[ast.List]


def test_scalars_still_work():
    assert json.loads(execute({"expression": "2 + 2"}))["result"] == 4


# ── read_document tells the model where its own edit went ──────

class _Head:
    def __init__(self, content_json, ident="rev-1"):
        self.content_json = content_json
        self.id = ident


class _Reports:
    """Just enough report service for DocOps.read()."""

    def __init__(self, head=None):
        self._head = head

    async def get(self, _user, _report):
        return type("R", (), {"title": "T", "abstract": ""})()

    async def draft_head(self, _user, _report):
        return self._head

    async def document_head(self, _report):
        return self._head


def _read(head):
    from src.assistant.doc_ops import DocOps  # pylint: disable=import-outside-toplevel
    return json.loads(asyncio.new_event_loop().run_until_complete(
        DocOps(_Reports(head), "u-1", "r-1").read()))


def test_an_empty_article_explains_that_proposals_are_pending():
    # The run that motivated this proposed a whole body, searched it for a
    # marker it had just written, was told "not in the body", re-read the
    # document, saw it empty -- and proposed the entire body again. The
    # note was true and answered the wrong question.
    note = _read(None)["note"]
    assert "no saved text yet" in note
    assert "do not re-propose" in note.lower()
    assert "pending, not lost" in note


def test_a_saved_article_carries_the_same_warning():
    doc = {"type": "doc", "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": "hello"}]}]}
    out = _read(_Head(doc))
    assert out["body_text"] == "hello"
    assert "do not re-propose" in out["note"].lower()


def test_the_mapping_iteration_4_wanted_is_now_simply_allowed():
    # Iteration 4 wrote `result = {...}` twice, was told "unsupported
    # syntax: Dict" twice, and fell back to one call per figure. Iteration 5
    # met a clearer message and still spent four calls on three numbers.
    # The shape itself was the problem, so the shape is now supported.
    out = json.loads(execute({"expression": "result = {'a': 1, 'b': 2}"}))
    assert out["result"] == {"a": 1, "b": 2}


def test_a_still_unsupported_node_names_the_rule_not_just_the_node():
    # The lesson survives the change: a refusal has to teach the shape that
    # works, or the model retries the same one. A set is still unsupported.
    err = json.loads(execute({"expression": "result = {1, 2}"}))["error"]
    assert "Set" in err
    assert "'before'" in err, "point at the shape that does work"
