"""Placing a widget by quoting the text it follows.

Why this exists, from the story-loop run of 2026-09-07T113157Z: the model
proposed a whole article, then called insert_studio_plot four times with no
position, and the four charts landed after the source line — in an article
whose prose says "Below: a direct comparison".

It was not being careless. Its own sequence was:

    44. replace_body(...)          -> {"proposed": true}
    45. read_document()            -> {"body_text": "", "body_text_length": 0}
    46. insert_studio_plot(...)    -- no at_char to give

A proposal is a card awaiting the user, so read_document returns the last
SAVED text. In the turn that writes the body there are no offsets to cite.
`after_text` is the way out: the model quotes a phrase, and the position is
worked out where the edit is applied, against the article as it then stands.
"""
# pylint: disable=missing-function-docstring
from __future__ import annotations

import asyncio

from src.assistant import doc_tools
from src.assistant.doc_edit import block_after_anchor
from src.assistant.tool_runtime import _resolve_at_block

DOC = {"type": "doc", "content": [
    {"type": "paragraph", "content": [{"type": "text", "text": "Intro."}]},
    {"type": "paragraph", "content": [
        {"type": "text", "text": "Below: a direct comparison of spending."}]},
    {"type": "paragraph", "content": [{"type": "text", "text": "Closing."}]},
]}


def test_the_widget_lands_after_the_quoted_paragraph():
    assert block_after_anchor(DOC, "Below: a direct comparison") == 2


def test_whitespace_and_case_do_not_have_to_match():
    # The anchor is prose that has been through HTML and TipTap by the time
    # it is a document; line breaks and space runs do not survive intact.
    assert block_after_anchor(DOC, "below:   A DIRECT\ncomparison") == 2


def test_the_first_match_wins():
    doc = {"type": "doc", "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": "the total"}]},
        {"type": "paragraph", "content": [{"type": "text", "text": "the total"}]}]}
    assert block_after_anchor(doc, "the total") == 1


def test_a_missing_anchor_is_none_so_the_caller_appends():
    # A chart in the wrong place is a worse article; a chart that never
    # arrives is a worse bug.
    assert block_after_anchor(DOC, "not in this article") is None
    assert block_after_anchor(DOC, "   ") is None
    assert block_after_anchor(DOC, "") is None


def test_it_reads_the_stored_wrapper_shape():
    # save_document stores {"tiptap": doc, "version": 2}. Reading `content`
    # off the wrapper is how every char-addressed tool once answered
    # "empty" -- this must not repeat it.
    assert block_after_anchor({"tiptap": DOC, "version": 2}, "Closing") == 3


def test_both_insert_verbs_offer_after_text():
    for name in ("mcp__gmr__insert_studio_plot", "mcp__gmr__insert_widget"):
        spec = next(t["function"] for t in doc_tools.DOC_TOOLS
                    if t["function"]["name"] == name)
        props = spec["parameters"]["properties"]
        assert "after_text" in props, name
        assert "PREFERRED" in props["after_text"]["description"], name
        # The model must be told WHY, or it keeps reaching for at_char and
        # measuring against text that is not there yet.
        assert "not accepted yet" in props["after_text"]["description"], name


def test_after_text_suppresses_the_server_side_at_block():
    # One instruction per card. at_char is measured against the last SAVED
    # text; if both are present the anchor is the fresher of the two, and
    # the applier should not have to arbitrate.
    args = {"at_char": 100, "after_text": "Below: a direct comparison"}
    out = asyncio.new_event_loop().run_until_complete(
        _resolve_at_block(_Doc(), "mcp__gmr__insert_studio_plot", args))
    assert out is None


def test_at_char_still_resolves_when_no_anchor_is_given():
    out = asyncio.new_event_loop().run_until_complete(
        _resolve_at_block(_Doc(), "mcp__gmr__insert_studio_plot", {"at_char": 3}))
    assert out == 1


class _Doc:
    async def content(self):
        return DOC
