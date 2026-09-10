"""The pacing note must not turn a JSON tool result into prose.

Regression for the whole of 2026-09-07 to 09-10, during which the promote
gate was red: ASSIST-23 drove a scripted agent that read a complete
investigate_entity payload, failed to parse it, and reported "no contract
count" for a company whose count was in the object. The result was fine;
the note concatenated onto it was not.
"""
from __future__ import annotations

import json

from src.assistant.tool_budget import attach_pacing, pacing_note, new_turn_budget


NOTE = ("\n\n[turn so far: 2 tool calls, 0s, 5913 of 14000 characters of "
        "tool output left. Propose your edits before it runs out.]")


class TestJsonSurvives:
    def test_an_object_result_still_parses(self):
        result = json.dumps({"label": "Company", "contract_count": 29})
        out = attach_pacing(result, NOTE)
        assert json.loads(out)["contract_count"] == 29

    def test_the_model_is_still_told_where_the_turn_stands(self):
        out = attach_pacing(json.dumps({"a": 1}), NOTE)
        assert "turn so far" in json.loads(out)["turn_status"]

    def test_no_note_leaves_the_result_byte_identical(self):
        result = json.dumps({"a": 1})
        assert attach_pacing(result, "") == result

    def test_prose_keeps_the_appended_form(self):
        assert attach_pacing("plain text answer", NOTE) == "plain text answer" + NOTE

    def test_a_truncated_object_is_not_made_worse(self):
        """Already-invalid JSON stays as it was — appending is no loss."""
        broken = '{"label": "Company", "contract_count"'
        assert attach_pacing(broken, NOTE) == broken + NOTE


class TestTheOriginalFailure:
    """The exact shape ASSIST-23 hit, end to end."""

    @staticmethod
    def _contract_count(raw):
        from src.assistant.mock_llm import _contract_count
        return _contract_count(raw)

    def test_the_scripted_agent_can_read_the_count_again(self):
        payload = json.dumps({
            "label": "Company",
            "entity_id": "2c1f32c2-90d0-5bb0-a080-791da65bedd8",
            "props": {"company_name": "Siemens AG", "contract_count": 29},
            "contract_count": 29,
        })
        assert self._contract_count(payload + NOTE) == "", (
            "concatenation is what broke it — if this ever passes, the "
            "regression this test guards is no longer reachable")
        assert self._contract_count(attach_pacing(payload, NOTE)) == "29"


class TestPacingStillFires:
    def test_no_note_below_the_threshold(self):
        budget = new_turn_budget(14_000)
        budget[0] = 13_000          # spent 1k of 14k
        assert pacing_note(budget, 1) == ""

    def test_a_note_once_half_the_budget_is_spent(self):
        budget = new_turn_budget(14_000)
        budget[0] = 5_913           # spent 8,087 — the real failing turn
        assert "turn so far" in pacing_note(budget, 2)
