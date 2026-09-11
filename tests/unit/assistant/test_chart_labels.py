"""A chart marker has to say which chart it is.

From the production conversation of 2026-09-11, "Spending with Israel".

The project was built the good way: four charts over ONE base query named
`il_contracts`. Every marker therefore read `[[chart N: il_contracts]]` —
a label compatible with every chart in the article.

The model had carried a belief from earlier in the conversation about which
chart the article already held. It read the marker, found nothing that
contradicted it, and wrote: "'il_contracts' is the source name (Plot 2's
source), confirming it's the count-by-buyer-country chart." The document
actually held the €M-by-buyer-country chart. So it inserted a duplicate of
the chart already there, never inserted the one it believed was there, and
left prose describing a chart the article does not contain.

The two charts differ exactly where a label can show it:

    contracts by buyer_country
    millions_eur by buyer_country

so a label naming the axes would have contradicted the belief outright.
"""
# pylint: disable=missing-function-docstring
from __future__ import annotations

from src.assistant import doc_edit


def _chart(title=None, x=None, y=None, source=None):
    attrs = {"widget_type": "pipeline", "schema_version": 1}
    if title:
        attrs["title"] = title
    if x or y:
        attrs["ui_params"] = {"x": x, "y": y}
    if source:
        attrs["data_params"] = {"sources": [{"name": source}]}
    return {"type": "widget", "attrs": attrs}


def _p(text):
    return {"type": "paragraph", "content": [{"type": "text", "text": text}]}


def test_the_two_israel_charts_no_longer_read_alike():
    value = _chart(x="buyer_country", y="millions_eur", source="il_contracts")
    count = _chart(x="buyer_country", y="contracts", source="il_contracts")
    assert doc_edit.label_for(value) != doc_edit.label_for(count)
    assert doc_edit.label_for(value) == "millions_eur by buyer_country"
    assert doc_edit.label_for(count) == "contracts by buyer_country"


def test_the_plot_name_wins_when_it_is_there():
    named = _chart(title="Number of EU contracts awarded to Israeli companies",
                   x="buyer_country", y="contracts", source="il_contracts")
    assert doc_edit.label_for(named).startswith("Number of EU contracts")


def test_it_still_falls_back_for_a_widget_with_neither():
    assert doc_edit.label_for(_chart(source="il_contracts")) == "il_contracts"
    assert doc_edit.label_for(_chart()) == "pipeline"


# ── resolving a marker ────────────────────────────────────────

DOC = {"type": "doc", "content": [
    _chart(x="buyer_country", y="millions_eur", source="il_contracts"),
    _chart(x="buyer_country", y="contracts", source="il_contracts"),
]}


def test_a_numbered_marker_still_resolves_by_number():
    out = doc_edit.restore_widgets([_p("[[chart 2: whatever]]")], DOC)
    assert out == [DOC["content"][1]]


def test_a_marker_with_no_number_resolves_by_label():
    # What a model has when the chart is not in the saved document yet: it
    # cannot count a chart it is inserting this turn.
    out = doc_edit.restore_widgets(
        [_p("[[chart: contracts by buyer_country]]")], DOC)
    assert out == [DOC["content"][1]]


def test_the_label_match_forgives_spacing_and_case():
    out = doc_edit.restore_widgets(
        [_p("[[chart:  CONTRACTS BY   buyer_country]]")], DOC)
    assert out == [DOC["content"][1]]


def test_a_number_past_the_end_falls_back_to_the_label():
    # A model numbering a chart it is inserting this turn guesses high. The
    # label is the better evidence; dropping the chart outright is worse.
    out = doc_edit.restore_widgets(
        [_p("[[chart 9: contracts by buyer_country]]")], DOC)
    assert out == [DOC["content"][1]]


def test_a_label_naming_nothing_is_still_dropped_not_printed():
    out = doc_edit.restore_widgets([_p("[[chart: no such chart]]"), _p("Tail.")], DOC)
    assert doc_edit.body_text({"type": "doc", "content": out}) == "Tail."


def test_body_text_labels_each_chart_distinctly():
    text = doc_edit.body_text(DOC)
    assert "[[chart 1: millions_eur by buyer_country]]" in text
    assert "[[chart 2: contracts by buyer_country]]" in text


# ── the marker an insert hands back ───────────────────────────

def test_insert_studio_plot_returns_a_marker_that_resolves():
    # The model's own words when it could not do this: "body_text only has
    # 1. So I can't. I have to wait for the user to apply the inserts."
    # It wrote the body first instead, describing four charts, and the
    # prose and the article have disagreed ever since.
    name = "Number of EU contracts awarded to Israeli companies, by buyer country"
    marker = f"[[chart: {name}]]"
    doc = {"type": "doc", "content": [_chart(title=name, x="buyer_country",
                                             y="contracts")]}
    out = doc_edit.restore_widgets([_p(marker)], doc)
    assert out == [doc["content"][0]], "the returned marker must resolve"
