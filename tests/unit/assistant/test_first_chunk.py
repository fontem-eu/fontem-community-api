"""The opening of an answer must reach the panel.

PydanticAI emits `part_start` carrying the first piece of a text part, then
`part_delta` events carrying the rest. The translator handled only the
deltas, so the beginning of EVERY assistant answer was dropped before it
left the server:

    the model said   "I cannot provide information about …"
    the panel showed   " cannot provide information about …"

It survived because the missing piece is always a plausible word and the
loss is invisible without knowing what was said. It took a scripted model,
whose exact sentence is known in advance, to expose it — ASSIST-23 asked
for a known answer and got one starting "urement contract(s) in the graph".

Live since the switch to this engine on 2026-08-12.
"""
# pylint: disable=protected-access
from __future__ import annotations

import json

from src.assistant.pydantic_ai_client import PydanticAIProxyClient


class _Delta:
    def __init__(self, content_delta):
        self.content_delta = content_delta


class _Ev:
    """A PydanticAI stream event, in the shape the translator reads."""

    def __init__(self, kind, *, part=None, delta=None):
        self.event_kind = kind
        self.part = part
        self.delta = delta


class _TextPart:
    def __init__(self, content):
        self.content = content


class _ToolPart:
    """A tool-call part puts structured arguments in `content`."""

    def __init__(self, args):
        self.content = args


def _state():
    return {"streaming": False, "text_len": 0, "name_cache": {}, "usage": None}


def _texts(events):
    client = PydanticAIProxyClient()
    state = _state()
    out = []
    for ev in events:
        for block in client._translate(ev, state, 0.0):
            for line in block.splitlines():
                if line.startswith("data: "):
                    payload = json.loads(line[6:])
                    if isinstance(payload, dict) and "text" in payload:
                        out.append(payload["text"])
    return out


class TestNothingIsLostFromTheStart:

    def test_the_first_piece_of_the_answer_is_sent(self):
        # The regression, in one assertion.
        events = [
            _Ev("part_start", part=_TextPart("I cannot")),
            _Ev("part_delta", delta=_Delta(" provide")),
            _Ev("part_delta", delta=_Delta(" information")),
        ]
        assert "".join(_texts(events)) == "I cannot provide information"

    def test_the_answer_does_not_begin_mid_word(self):
        # The shape of the failure as it reached the panel: a leading space
        # where a word should be.
        events = [
            _Ev("part_start", part=_TextPart("Siemens AG has 8 EU proc")),
            _Ev("part_delta", delta=_Delta("urement contract(s)")),
        ]
        assembled = "".join(_texts(events))
        assert assembled.startswith("Siemens AG")
        assert not assembled.startswith("urement")

    def test_a_tool_call_part_is_not_streamed_as_prose(self):
        # A tool-call part carries structured arguments in `content`.
        # Streaming those would print JSON into the conversation.
        events = [_Ev("part_start", part=_ToolPart({"query": "Siemens"}))]
        assert not _texts(events)

    def test_a_part_that_starts_empty_says_nothing(self):
        events = [
            _Ev("part_start", part=_TextPart("")),
            _Ev("part_delta", delta=_Delta("hello")),
        ]
        assert "".join(_texts(events)) == "hello"

    def test_the_streaming_status_still_leads_the_first_chunk(self):
        # The panel switches out of "connecting" on that status; if the
        # first text now arrives on part_start, the status has to come with
        # it rather than waiting for a delta that may never arrive.
        client = PydanticAIProxyClient()
        state = _state()
        blocks = client._translate(
            _Ev("part_start", part=_TextPart("Hello")), state, 0.0)
        assert "event: status" in blocks[0]
        assert "event: chunk" in blocks[1]
        assert state["streaming"] is True

    def test_a_one_shot_answer_with_no_deltas_still_arrives(self):
        # Short answers can be a single part_start and nothing else. Before
        # this, such a turn produced no text at all.
        assert "".join(_texts([
            _Ev("part_start", part=_TextPart("Yes."))])) == "Yes."

    def test_unrelated_events_are_still_ignored(self):
        assert not _texts([_Ev("part_end"), _Ev("final_result")])
