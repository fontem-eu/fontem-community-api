"""The rolling summary: produced only on overflow, and only once per turn.

The expensive half of the continuity window. Everything here is about it
staying cheap — a conversation that fits pays nothing, and one that overflows
pays for what just fell off rather than for its whole history.
"""
# pylint: disable=missing-function-docstring
from __future__ import annotations

from src.assistant import summariser
from src.assistant.context import Turn


def _turn(role: str, content: str, mid: str = "") -> Turn:
    return Turn(role=role, content=content, message_id=mid)


class TestUnsummarised:

    def test_no_marker_means_everything_is_still_live(self):
        history = [_turn("user", "a", "1"), _turn("assistant", "b", "2")]
        assert summariser.unsummarised(history, "") == history

    def test_the_marker_excludes_itself_and_everything_before_it(self):
        history = [
            _turn("user", "a", "1"),
            _turn("assistant", "b", "2"),
            _turn("user", "c", "3"),
        ]
        assert summariser.unsummarised(history, "2") == [history[2]]

    def test_a_marker_at_the_end_leaves_nothing(self):
        history = [_turn("user", "a", "1"), _turn("assistant", "b", "2")]
        assert summariser.unsummarised(history, "2") == []

    def test_an_unknown_marker_is_treated_as_covering_nothing(self):
        # The row it named is gone. Re-summarising material already
        # represented is wasteful; the other reading silently drops turns the
        # summary never saw.
        history = [_turn("user", "a", "1"), _turn("assistant", "b", "2")]
        assert summariser.unsummarised(history, "deleted") == history

    def test_turns_are_summarised_once_across_successive_overflows(self):
        # The bug this exists to catch: re-deriving the window from the whole
        # conversation folds the same turns in again on every message, and the
        # summary restates its own contents.
        history = [_turn("user", f"m{i}", str(i)) for i in range(6)]

        first = summariser.unsummarised(history, "")
        assert len(first) == 6
        # ...turns 0-2 fall off and are summarised through message "2".
        second = summariser.unsummarised(history, "2")
        assert [t.message_id for t in second] == ["3", "4", "5"]
        assert all(t.message_id not in {"0", "1", "2"} for t in second)


class TestBuildRequest:

    def test_nothing_dropped_means_no_call(self):
        assert summariser.build_request("prior note", []) == ""

    def test_the_previous_summary_is_folded_in_not_replaced(self):
        req = summariser.build_request("prior note", [_turn("user", "new thing")])
        assert "prior note" in req
        assert "new thing" in req

    def test_the_first_overflow_has_no_previous_summary(self):
        req = summariser.build_request("", [_turn("user", "new thing")])
        assert "Earlier summary" not in req
        assert "new thing" in req

    def test_a_tool_turn_is_labelled_as_a_tool(self):
        req = summariser.build_request(
            "", [Turn(role="tool", content="3 hits", name="search")],
        )
        assert "Tool search: 3 hits" in req

    def test_a_blanked_tool_turn_still_says_it_ran(self):
        req = summariser.build_request(
            "", [Turn(role="tool", content="", name="search")],
        )
        assert "Tool search: (called)" in req


class TestCap:

    def test_a_summary_within_the_limit_is_untouched(self):
        assert summariser.cap("  short  ") == "short"

    def test_an_overlong_summary_is_cut(self):
        text = "x" * (summariser.MAX_SUMMARY_CHARS + 500)
        assert len(summariser.cap(text)) == summariser.MAX_SUMMARY_CHARS

    def test_nothing_is_an_empty_summary_not_a_crash(self):
        assert summariser.cap("") == ""
        assert summariser.cap(None) == ""


class TestRender:

    def test_the_summary_is_marked_as_a_note_not_as_dialogue(self):
        turn = summariser.render("they chose the graph view")
        assert turn.content.startswith(summariser.SUMMARY_PREFIX)
        assert "they chose the graph view" in turn.content
