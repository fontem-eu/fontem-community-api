"""A chart is text the model can read, keep, move, or delete.

Run 6 created three charts, inserted all three, and shipped an article with
two. Turn 2 called `replace_body` — and a whole-body rewrite is HTML, which
cannot carry a widget's data_params, so every chart already embedded was
dropped on the way in.

The model could not have avoided it. `read_document` rendered a widget as
the empty string, so its own charts appeared as blank gaps: they existed in
nothing it could see and nothing it could say. (In an earlier run it tried
to invent this mechanism, writing `[[SECTOR_CHART]]` into a draft and then
searching for it.)

So the chart is text now, and the round trip is what these pin.
"""
# pylint: disable=missing-function-docstring
from __future__ import annotations

from src.assistant import doc_edit


def _texts(blocks):
    """The blocks' text, through the public reader."""
    return doc_edit.body_text({"type": "doc", "content": blocks}).split("\n\n")


def _p(text):
    return {"type": "paragraph", "content": [{"type": "text", "text": text}]}


def _chart(name):
    return {"type": "widget", "attrs": {
        "widget_type": "pipeline", "schema_version": 1,
        "data_params": {"sources": [{"name": name}]},
        "ui_params": {"chart": "bar_h"}}}


DOC = {"type": "doc", "content": [
    _p("Spending fell after February 2022."),
    _chart("by_country"),
    _p("The sector split tells the same story."),
    _chart("by_sector"),
]}


def test_the_model_can_see_its_charts():
    text = doc_edit.body_text(DOC)
    assert "[[chart 1: by_country]]" in text
    assert "[[chart 2: by_sector]]" in text


def test_a_rewrite_that_keeps_the_markers_keeps_the_charts():
    # The exact failure from run 6, in miniature.
    rewritten = [_p("New opening."), _p("[[chart 1: by_country]]"),
                 _p("New middle."), _p("[[chart 2: by_sector]]")]
    out = doc_edit.restore_widgets(rewritten, DOC)
    charts = [b for b in out if b.get("type") == "widget"]
    assert len(charts) == 2
    # The node itself is reused, so the chart is identical, not equivalent.
    assert charts[0] is DOC["content"][1]
    assert charts[1] is DOC["content"][3]


def test_dropping_a_marker_removes_that_chart_on_purpose():
    # The other half of what markers buy: before this, deleting a chart was
    # not expressible at all.
    out = doc_edit.restore_widgets([_p("Only the sector chart."),
                                    _p("[[chart 2: by_sector]]")], DOC)
    charts = [b for b in out if b.get("type") == "widget"]
    assert len(charts) == 1 and charts[0] is DOC["content"][3]


def test_a_marker_written_mid_sentence_still_becomes_a_chart():
    out = doc_edit.restore_widgets([_p("As [[chart 1: by_country]] shows.")], DOC)
    assert [b["type"] for b in out] == ["paragraph", "widget", "paragraph"]


def test_a_marker_for_a_chart_that_does_not_exist_is_dropped():
    # Not printed. Brackets in a published article help nobody.
    out = doc_edit.restore_widgets([_p("[[chart 9: invented]]"), _p("Tail.")], DOC)
    assert [b["type"] for b in out] == ["paragraph"]
    assert doc_edit.body_text({"type": "doc", "content": out}) == "Tail."


def test_a_rewrite_of_an_article_with_no_charts_keeps_its_prose():
    blocks = [_p("Just prose.")]
    out = doc_edit.restore_widgets(blocks, {"type": "doc", "content": []})
    assert _texts(out) == ["Just prose."]


def test_a_marker_is_removed_even_when_there_are_no_charts_at_all():
    # This used to return early, so a marker in the incoming body was
    # passed through as TEXT. Iteration 7 saved literal
    # `[[chart 1: EU public spending on Russian suppliers...]]` into an
    # article, printed beside the chart it was meant to be. Losing a chart
    # is bad; rendering the plumbing is worse.
    out = doc_edit.restore_widgets(
        [_p("Prose."), _p("[[chart 1: not here]]"), _p("Tail.")],
        {"type": "doc", "content": []})
    assert _texts(out) == ["Prose.", "Tail."]
    assert "[[chart" not in "".join(_texts(out))


# ── charts are atomic under replace_part ──────────────────────

def test_a_span_that_cuts_a_marker_is_refused():
    # Splicing half a marker leaves `[[chart 1: by_c` in the article: text
    # that renders as nothing and restores as nothing.
    out = doc_edit.replace_span(DOC, 0, 45, "Something else.")
    assert "cuts through the chart" in out["error"]
    assert "atomic" in out["error"]


def test_a_span_covering_a_whole_marker_replaces_that_chart():
    spans = doc_edit.block_spans(DOC)
    start, end = spans[1]
    out = doc_edit.replace_span(DOC, start, end, "A table would be clearer.")
    kinds = [b["type"] for b in out["content"]]
    assert kinds == ["paragraph", "paragraph", "paragraph", "widget"]


def test_an_empty_replacement_over_a_marker_deletes_the_chart():
    spans = doc_edit.block_spans(DOC)
    start, end = spans[1]
    out = doc_edit.replace_span(DOC, start, end, "")
    assert [b["type"] for b in out["content"]] == ["paragraph", "paragraph", "widget"]


def test_editing_prose_leaves_the_charts_alone():
    out = doc_edit.replace_span(DOC, 0, 34, "Spending collapsed.")
    assert [b["type"] for b in out["content"]] == [
        "paragraph", "widget", "paragraph", "widget"]
