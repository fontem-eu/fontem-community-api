"""What investigate_entity hands the model.

Three defects, all found by ASSIST-23 once it ran on a deterministic model
against fontem-testing rather than on a 1.7B's judgement. Measured on
Siemens AG (entity b559559e-…):

1. The reported contract count was the size of the page just fetched, not
   the entity's real total. The summary read "with 5 EU procurement
   contract(s) in the graph" while the graph held 8 — and the true figure
   was already in props["contract_count"], unread. Every entity with more
   contracts than `contract_limit` was understated, confidently, in prose.
2. The result was 34,561 characters against a 14,000-character per-turn
   budget, so it reached the model truncated mid-JSON. The graph
   neighbourhood was 26,764 of that, almost all per-node property bags.
3. props["recent_contracts"] was byte-for-byte the result's own
   `contracts` list: 3,677 characters of the budget spent twice.

The old version of ASSIST-23 could not catch any of these — it asserted
that the answer contained a digit, and "5" is a digit.
"""
# pylint: disable=protected-access
from __future__ import annotations

import asyncio
import json

from src.assistant import tool_budget
from src.assistant.tool_runtime import ToolRuntime


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _Resp:
    def __init__(self, payload, status=200):
        self.status_code, self._payload = status, payload

    def json(self):
        return self._payload


def _contract_row(title: str) -> dict:
    """A TED row the way the API sends it: a few values, mostly nulls."""
    row = {k: None for k in (
        "ted_notice_id", "ted_publication_number", "value_eur",
        "estimated_value_eur", "notice_type", "value_currency",
        "value_original", "value_before_eur", "value_confidence",
        "value_quarantined", "value_quarantine_reason", "award_date",
        "procedure_type", "ted_url", "modifies_publication_number")}
    row.update({"title": title, "cpv": "45315100",
                "authority": "Bezirksklinikum Mittelfranken",
                "authority_id": "ad713744-e19a-51ed-9e5c-66d4d60b73b8",
                "value_low_confidence": False})
    return row


#: Eight in the graph, five on the page — the exact shape of the bug.
CONTRACTS = [_contract_row(f"Contract {i}") for i in range(5)]
PROPS = {
    "gmr_id": "b559559e-6158-5868-a28c-90b4805bc7f0",
    "company_name": "Siemens AG",
    "country": "DEU",
    "contract_count": 8,
    "total_contract_value_eur": 6735232.39,
    "recent_contracts": CONTRACTS,
    "directors": [],
}
GRAPH = {
    "center": "b559559e-6158-5868-a28c-90b4805bc7f0",
    "nodes": [{"id": f"n{i}", "label": f"Neighbour {i}", "type": "Company",
               # The property bag that made this 22k on the real entity.
               "properties": {f"field_{j}": "x" * 40 for j in range(20)}}
              for i in range(31)],
    "edges": [{"source": "n0", "target": f"n{i}", "type": "AWARDED_TO",
               "properties": {f"e_{j}": "y" * 30 for j in range(10)}}
              for i in range(30)],
    "truncated": False,
    "total_available": 31,
}


class _Client:
    async def get(self, url, params=None, **_):
        del params
        if "/contracts" in url:
            return _Resp(CONTRACTS)
        if "/graph/" in url:
            return _Resp(GRAPH)
        if "/companies/" in url:
            return _Resp(PROPS)
        return _Resp({"authority_id": "x", "authority_name": None})


def _investigate(**kw):
    runtime = ToolRuntime(gmr_api_url="http://fake")
    raw = _run(runtime._investigate(_Client(), PROPS["gmr_id"], **kw))
    return raw, json.loads(raw)


class TestTheCountIsTheGraphsNotThePages:

    def test_contract_count_is_the_entitys_total(self):
        _, out = _investigate(contract_limit=5)
        assert out["contract_count"] == 8, (
            "the reported count must be the graph's total, not the number "
            "of rows this call happened to fetch")

    def test_the_page_size_is_reported_separately(self):
        # So the model can say "showing 5 of 8" instead of describing a
        # page as the whole set.
        _, out = _investigate(contract_limit=5)
        assert out["contracts_shown"] == 5
        assert len(out["contracts"]) == 5

    def test_the_summary_quotes_the_real_number(self):
        # The user-visible half: this sentence is what the assistant reads
        # out, and it said 5 for an entity with 8.
        _, out = _investigate(contract_limit=5)
        assert "8 EU procurement contract(s)" in out["summary"]
        assert "5 EU procurement contract(s)" not in out["summary"]

    def test_it_falls_back_to_the_page_when_the_total_is_missing(self):
        # Not every profile carries a count; better the page size than none.
        class _NoCount(_Client):
            async def get(self, url, params=None, **_):
                # `/contracts` first: the contracts URL is
                # /companies/<id>/contracts, so a `/companies/` check placed
                # ahead of it answers the contracts request with a profile.
                # The same shape of mistake as the 200-skeleton bug — a fake
                # has to answer per path, not per substring.
                if "/companies/" in url and "/contracts" not in url:
                    return _Resp({k: v for k, v in PROPS.items()
                                  if k != "contract_count"})
                return await super().get(url, params=params)

        runtime = ToolRuntime(gmr_api_url="http://fake")
        out = json.loads(_run(runtime._investigate(
            _NoCount(), PROPS["gmr_id"], contract_limit=5)))
        assert out["contract_count"] == 5


class TestItFitsWhatTheModelCanRead:

    def test_the_result_fits_the_cap_on_a_single_tool_result(self):
        # THE binding ceiling, and the one the first fix missed. There are
        # two: MAX_TOOL_RESULT_CHARS caps ONE result (8,000);
        # MAX_TOOL_RESULT_CHARS_PER_TURN caps all of them together
        # (14,000). Asserting the larger one passed while the real dispatch
        # still truncated at 8,000 — the payload was 10,898 and reached the
        # model ending mid-object, which is exactly the failure this test
        # was written to stop.
        raw, _ = _investigate(contract_limit=5)
        assert len(raw) < tool_budget.MAX_TOOL_RESULT_CHARS, (
            f"{len(raw)} chars against the {tool_budget.MAX_TOOL_RESULT_CHARS} "
            "per-result cap — the dispatch would truncate this mid-JSON")

    def test_it_also_leaves_room_in_the_turn_for_other_calls(self):
        # A turn is search + investigate + whatever follows. If one call
        # eats the turn's budget the rest come back as "budget spent".
        raw, _ = _investigate(contract_limit=5)
        assert len(raw) < tool_budget.MAX_TOOL_RESULT_CHARS_PER_TURN * 0.7

    def test_it_is_still_valid_json_at_that_size(self):
        raw, out = _investigate(contract_limit=5)
        assert json.loads(raw) == out

    def test_the_contracts_are_not_sent_twice(self):
        _, out = _investigate(contract_limit=5)
        assert "recent_contracts" not in out["props"]
        assert out["props"]["contract_count"] == 8, "the rest of props stays"

    def test_empty_contract_fields_are_dropped(self):
        _, out = _investigate(contract_limit=5)
        row = out["contracts"][0]
        assert "title" in row and "authority_id" in row
        assert "ted_url" not in row, "null fields cost context and say nothing"
        # False is a value, not an absence.
        assert row["value_low_confidence"] is False

    def test_the_neighbourhood_keeps_its_shape_without_the_property_bags(self):
        _, out = _investigate(contract_limit=5)
        graph = out["graph"]
        assert graph["node_count"] == 31 and graph["edge_count"] == 30
        assert graph["nodes"][0]["id"] == "n0"
        assert graph["nodes"][0]["label"] == "Neighbour 0"
        assert "properties" not in graph["nodes"][0], (
            "a neighbour's full record belongs in its own investigate call")
        assert "properties" not in graph["edges"][0]

    def test_a_sampled_neighbourhood_says_so_and_still_reports_the_totals(self):
        # 31 nodes with UUIDs and company names do not fit the graph's
        # character budget, so some are dropped — and the model is told,
        # with the real totals, rather than left to read a sample as the
        # whole neighbourhood.
        _, out = _investigate(contract_limit=5)
        graph = out["graph"]
        assert graph["node_count"] == 31 and graph["edge_count"] == 30
        if len(graph["nodes"]) < 31 or len(graph["edges"]) < 30:
            assert graph["truncated"] is True
            assert "investigate a specific id" in graph["note"]

    def test_the_neighbourhood_is_budgeted_by_size_not_by_count(self):
        # A count cap is what failed: 31 neighbours sat under a 40-node cap
        # and still came to 9,158 characters, because a node costs a
        # 36-character UUID plus a name.
        _, out = _investigate(contract_limit=5)
        assert len(json.dumps(out["graph"])) < 5_000
