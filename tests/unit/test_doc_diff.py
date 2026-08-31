"""Block differences, which is what a reviewer actually reads.

A character diff of serialised TipTap JSON is unreadable and a diff that
reports every block below an insertion as "changed" is worse than none —
it buries the one real edit. These pin the matching behaviour that makes
the review screen worth looking at.
"""
# pylint: disable=missing-function-docstring
from __future__ import annotations

from src.services import doc_diff


def _doc(*paragraphs: str) -> dict:
    return {"version": 2, "tiptap": {"type": "doc", "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": p}]}
        for p in paragraphs
    ]}}


def _ops(before, after):
    return [(o["op"], (o["after"] or o["before"])["text"])
            for o in doc_diff.diff(before, after)]


def test_an_insertion_does_not_change_everything_below_it():
    # The failure that makes a naïve positional diff useless: add a lead
    # paragraph and every paragraph after it reports as rewritten.
    ops = _ops(_doc("a", "b", "c"), _doc("lead", "a", "b", "c"))
    assert ops == [("insert", "lead"), ("equal", "a"),
                   ("equal", "b"), ("equal", "c")]


def test_a_rewrite_pairs_the_old_block_with_the_new_one():
    result = doc_diff.diff(_doc("a", "old"), _doc("a", "new"))
    replaced = [o for o in result if o["op"] == "replace"]
    assert len(replaced) == 1
    assert replaced[0]["before"]["text"] == "old"
    assert replaced[0]["after"]["text"] == "new"


def test_a_deletion_is_reported_once():
    assert _ops(_doc("a", "gone", "b"), _doc("a", "b")) == [
        ("equal", "a"), ("delete", "gone"), ("equal", "b")]


def test_an_unequal_replace_run_keeps_every_block():
    # Two blocks become three: nothing may be silently dropped from the
    # diff, or a reviewer approves an edit they were never shown.
    ops = _ops(_doc("a", "x", "y", "b"), _doc("a", "p", "q", "r", "b"))
    kinds = [op for op, _ in ops]
    assert kinds.count("insert") == 1
    assert kinds.count("replace") == 2
    assert [t for _, t in ops if t in ("p", "q", "r")] == ["p", "q", "r"]


def test_a_widget_that_repoints_is_a_change_not_an_empty_block():
    """Atoms carry no text. Comparing on text alone would show two blank
    blocks and call a repointed widget unchanged."""
    def widget(entity):
        return {"version": 2, "tiptap": {"type": "doc", "content": [
            {"type": "widget", "attrs": {"widget_type": "graph_explorer",
                                         "entityId": entity}},
        ]}}
    result = doc_diff.diff(widget("entity-a"), widget("entity-b"))
    assert [o["op"] for o in result] == ["replace"]
    assert "entity-a" in result[0]["before"]["label"]
    assert "entity-b" in result[0]["after"]["label"]


def test_headings_are_distinguished_from_paragraphs_with_the_same_words():
    para = {"version": 2, "tiptap": {"type": "doc", "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": "Costs"}]}]}}
    head = {"version": 2, "tiptap": {"type": "doc", "content": [
        {"type": "heading", "attrs": {"level": 2},
         "content": [{"type": "text", "text": "Costs"}]}]}}
    assert [o["op"] for o in doc_diff.diff(para, head)] == ["replace"]


def test_an_empty_or_missing_document_is_all_insertions():
    assert [o["op"] for o in doc_diff.diff(None, _doc("first"))] == ["insert"]
    assert not doc_diff.diff(None, None)


def test_a_bare_tiptap_document_works_too():
    """Callers hold both the stored wrapper and the bare document."""
    bare = _doc("a")["tiptap"]
    assert [o["op"] for o in doc_diff.diff(bare, _doc("a", "b"))] == [
        "equal", "insert"]


def test_the_summary_counts_what_a_history_row_shows():
    ops = doc_diff.diff(_doc("a", "b", "gone"), _doc("a", "B!", "new", "also"))
    counts = doc_diff.summary(ops)
    assert counts["changed"] >= 1
    assert counts["added"] >= 1
    assert sum(counts.values()) == len(
        [o for o in ops if o["op"] != "equal"])


def test_an_atom_carries_its_attributes_so_it_can_be_rendered():
    """Reviewing "widget:graph_explorer:00d87075" is not reviewing the
    thing that will be published. The attrs ride along so the review
    screen can render the actual widget."""
    doc = {"version": 2, "tiptap": {"type": "doc", "content": [
        {"type": "widget", "attrs": {"widget_type": "graph_explorer",
                                     "entityId": "e-1", "depth": 2}},
        {"type": "paragraph", "content": [{"type": "text", "text": "prose"}]},
    ]}}
    widget, prose = doc_diff.blocks(doc)
    assert widget["attrs"]["widget_type"] == "graph_explorer"
    assert widget["attrs"]["entityId"] == "e-1"
    # Prose describes itself; it needs no attrs.
    assert "attrs" not in prose


def test_a_changed_widget_carries_both_sides_attributes():
    def widget(entity):
        return {"version": 2, "tiptap": {"type": "doc", "content": [
            {"type": "widget", "attrs": {"widget_type": "contracts_table",
                                         "entityId": entity}}]}}
    op = doc_diff.diff(widget("before-id"), widget("after-id"))[0]
    assert op["before"]["attrs"]["entityId"] == "before-id"
    assert op["after"]["attrs"]["entityId"] == "after-id"
