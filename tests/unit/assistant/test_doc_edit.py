"""The coordinate space character-addressed edits are written against.

Every number here is a character offset into `body_text`, so the tests
spell out what that string is rather than trusting the reader to count.
The point of the module is that a model can change one sentence without
restating the article, and the risk is that an off-by-one silently eats a
neighbouring paragraph -- so the boundaries are what get pinned.
"""
# pylint: disable=protected-access
from __future__ import annotations

from src.assistant import doc_edit


def _p(text: str) -> dict:
    return {"type": "paragraph", "content": [{"type": "text", "text": text}]}


def _widget() -> dict:
    return {"type": "widget",
            "attrs": {"widget_type": "pipeline", "schema_version": 1}}


#: "Alpha one." + "\n\n" + "Beta two." -> offsets 0..10, 12..21
DOC = {"type": "doc", "content": [_p("Alpha one."), _p("Beta two.")]}


class TestTheCoordinateSpace:

    def test_body_text_joins_blocks_with_a_blank_line(self):
        assert doc_edit.body_text(DOC) == "Alpha one.\n\nBeta two."

    def test_block_spans_are_the_offsets_of_each_block(self):
        assert doc_edit.block_spans(DOC) == [(0, 10), (12, 21)]

    def test_a_widget_occupies_a_position_but_no_characters(self):
        doc = {"type": "doc", "content": [_p("Before."), _widget(), _p("After.")]}
        # "Before." 0..7, widget 9..9, "After." 11..17
        assert doc_edit.block_spans(doc) == [(0, 7), (9, 9), (11, 17)]
        assert doc_edit.body_text(doc) == "Before.\n\n\n\nAfter."

    def test_a_bare_block_list_is_accepted_as_a_document(self):
        # read_document hands back whatever was stored; early drafts are a
        # list of blocks rather than a {"type": "doc"} wrapper.
        assert doc_edit.body_text([_p("Alpha one."), _p("Beta two.")]) == \
            "Alpha one.\n\nBeta two."


class TestFind:

    def test_it_reports_the_offsets_of_the_first_occurrence(self):
        assert doc_edit.find(DOC, "Beta") == {
            "found": True, "start": 12, "end": 16, "count": 1}

    def test_it_says_how_many_times_the_needle_occurs(self):
        doc = {"type": "doc", "content": [_p("gas gas"), _p("gas")]}
        out = doc_edit.find(doc, "gas")
        assert out["count"] == 3, (
            "a model told only where the first match is will edit that one "
            "and believe it edited the one it meant"
        )

    def test_a_missing_needle_is_not_an_error_but_a_finding(self):
        out = doc_edit.find(DOC, "Gazprom")
        assert out["found"] is False and out["count"] == 0
        assert "error" not in out

    def test_an_empty_needle_is_refused(self):
        assert "error" in doc_edit.find(DOC, "")


class TestReplaceSpanWithinOneBlock:

    def test_it_replaces_exactly_the_span(self):
        out = doc_edit.replace_span(DOC, 0, 5, "Omega")
        assert doc_edit.body_text(out) == "Omega one.\n\nBeta two."

    def test_it_leaves_the_other_blocks_untouched(self):
        out = doc_edit.replace_span(DOC, 12, 16, "Gamma")
        assert doc_edit.body_text(out) == "Alpha one.\n\nGamma two."

    def test_an_empty_replacement_deletes_the_span(self):
        out = doc_edit.replace_span(DOC, 5, 10, "")
        assert doc_edit.body_text(out) == "Alpha\n\nBeta two."

    def test_it_keeps_the_marks_of_the_text_it_lands_in(self):
        doc = {"type": "doc", "content": [{
            "type": "paragraph",
            "content": [{"type": "text", "text": "very bold claim",
                         "marks": [{"type": "bold"}]}]}]}
        out = doc_edit.replace_span(doc, 5, 9, "loud")
        node = out["content"][0]["content"][1]
        assert node["text"] == "loud"
        assert node["marks"] == [{"type": "bold"}], (
            "replacing words inside a bold run must stay bold; dropping the "
            "mark is a silent formatting change the user did not ask for"
        )

    def test_emptying_a_paragraph_removes_it(self):
        out = doc_edit.replace_span(DOC, 0, 10, "")
        assert doc_edit.body_text(out) == "Beta two."
        assert len(out["content"]) == 1


class TestReplaceSpanAcrossBlocks:

    def test_it_replaces_every_touched_block(self):
        out = doc_edit.replace_span(DOC, 5, 16, "One paragraph now.")
        assert doc_edit.body_text(out) == "One paragraph now."

    def test_blank_lines_in_the_replacement_start_new_paragraphs(self):
        out = doc_edit.replace_span(DOC, 0, 21, "First.\n\nSecond.")
        assert [b["type"] for b in out["content"]] == ["paragraph", "paragraph"]
        assert doc_edit.body_text(out) == "First.\n\nSecond."

    def test_a_span_landing_in_the_separator_inserts_between_blocks(self):
        # 10..11 is inside "\n\n" -- no block's text is touched.
        out = doc_edit.replace_span(DOC, 10, 11, "Middle.")
        assert doc_edit.body_text(out) == "Alpha one.\n\nMiddle.\n\nBeta two."


class TestReplaceSpanRefusals:

    def test_a_span_past_the_end_is_refused_with_the_real_length(self):
        out = doc_edit.replace_span(DOC, 0, 999, "x")
        assert "error" in out and "21 characters" in out["error"], (
            "the model needs the actual length to correct its next attempt"
        )

    def test_a_reversed_span_is_refused(self):
        assert "error" in doc_edit.replace_span(DOC, 9, 2, "x")

    def test_a_negative_start_is_refused(self):
        assert "error" in doc_edit.replace_span(DOC, -1, 4, "x")


class TestBlockIndexAt:
    """Where a widget goes. The answer is an INSERTION index."""

    def test_zero_puts_it_before_everything(self):
        assert doc_edit.block_index_at(DOC, 0) == 0

    def test_a_character_inside_a_block_inserts_after_that_block(self):
        assert doc_edit.block_index_at(DOC, 4) == 1, (
            "pointing at a paragraph means 'put the chart after this one'"
        )

    def test_the_end_of_the_body_appends(self):
        assert doc_edit.block_index_at(DOC, 21) == 2

    def test_past_the_end_still_appends_rather_than_failing(self):
        assert doc_edit.block_index_at(DOC, 10_000) == 2

    def test_an_empty_document_takes_the_first_slot(self):
        assert doc_edit.block_index_at({"type": "doc", "content": []}, 0) == 0


class TestTheStoredDocumentShape:
    """save_document stores {"tiptap": doc, "version": 2}, not the doc.

    Reading `content` off that wrapper finds nothing, so every
    character-addressed tool answered as though the article were empty. A
    blank article makes that indistinguishable from correct, which is why
    it survived a whole run: only once there was text did
    find_in_document start reporting "not in the body" for every
    substring, including "the", and replace_part refuse spans against a
    body it measured as 0 characters. The model spent ten calls on it.
    """

    STORED = {"version": 2, "tiptap": {"type": "doc", "content": [
        _p("Alpha one."), _p("Beta two.")]}}

    def test_the_text_is_found_through_the_wrapper(self):
        assert doc_edit.body_text(self.STORED) == "Alpha one.\n\nBeta two."

    def test_find_works_through_the_wrapper(self):
        assert doc_edit.find(self.STORED, "Beta")["start"] == 12

    def test_offsets_are_the_same_wrapped_or_not(self):
        assert doc_edit.block_spans(self.STORED) == doc_edit.block_spans(DOC), (
            "a coordinate space that depends on how the caller happened to "
            "wrap the document is not a coordinate space"
        )

    def test_an_edit_comes_back_stored_shaped(self):
        out = doc_edit.replace_span(self.STORED, 0, 5, "Omega")
        assert out["version"] == 2 and "tiptap" in out, (
            "handing the editor a bare doc where it stored a wrapper would "
            "replace the wrapper itself"
        )
        assert doc_edit.body_text(out) == "Omega one.\n\nBeta two."

    def test_block_index_is_the_same_wrapped_or_not(self):
        assert (doc_edit.block_index_at(self.STORED, 4)
                == doc_edit.block_index_at(DOC, 4))
