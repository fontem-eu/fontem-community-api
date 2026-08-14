"""A Studio write that does not work must not be reported as a success.

The reported problem: the agent wrote a query, the tool said "created", and
the project kept a query that does not parse. Nobody ran it, so nothing
noticed until a human opened the project.

Two rules, and the tests are mostly about the second:

* A query the engine rejects is NOT saved, and the engine's own words go
  back to the agent so it can fix and retry.
* A query we could not CHECK — proxy down, timeout, network — IS saved,
  with a warning. Failing closed on an unrelated outage would tell the
  agent its query is wrong, and it would "fix" a query that was correct.
"""
# pylint: disable=protected-access,redefined-outer-name
from __future__ import annotations

import asyncio
import json

import pytest

from src.assistant.studio_ops import StudioOps
from src.services import studio_validation


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _Resp:
    def __init__(self, status, payload=None, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text or json.dumps(payload)

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class _Client:
    """Records what was asked of the engine and answers as told."""

    def __init__(self, answers=None, boom=None):
        self.posts: list[tuple[str, dict]] = []
        self._answers = answers or {}
        self._boom = boom

    async def post(self, url, json=None, timeout=None):  # noqa: A002
        del timeout
        self.posts.append((url, json or {}))
        if self._boom:
            raise self._boom
        for fragment, answer in self._answers.items():
            if fragment in url:
                return answer
        return _Resp(200, {"columns": [], "rows": []})


# ── how a query is checked ─────────────────────────────────────


class TestQueryValidation:

    def test_cypher_is_planned_not_run(self):
        # EXPLAIN is the whole trick: the real engine parses, resolves
        # labels and plans, and executes nothing.
        client = _Client()
        _run(studio_validation.validate_query(
            client, "http://api", "cypher", "MATCH (c:Company) RETURN c"))
        url, body = client.posts[0]
        assert url.endswith("/query/cypher")
        assert body["query"].startswith("EXPLAIN ")
        assert "MATCH (c:Company)" in body["query"]

    def test_sql_is_planned_too(self):
        client = _Client()
        _run(studio_validation.validate_query(
            client, "http://api", "sql", "SELECT 1"))
        assert client.posts[0][1]["query"].startswith("EXPLAIN ")

    def test_sparql_is_executed_because_there_is_no_explain(self):
        client = _Client()
        _run(studio_validation.validate_query(
            client, "http://api", "sparql", "SELECT * WHERE { ?s ?p ?o }"))
        url, body = client.posts[0]
        assert url.endswith("/sparql")
        assert not body["query"].upper().startswith("EXPLAIN")

    def test_a_rejected_query_is_invalid_and_carries_the_reason(self):
        client = _Client({"cypher": _Resp(
            400, {"detail": "Invalid input 'MTCH': expected a clause"})})
        verdict = _run(studio_validation.validate_query(
            client, "http://api", "cypher", "MTCH (c) RETURN c"))
        assert not verdict.ok
        assert verdict.checked
        assert "MTCH" in verdict.errors[0]

    def test_an_unknown_language_is_refused_without_asking_anyone(self):
        client = _Client()
        verdict = _run(studio_validation.validate_query(
            client, "http://api", "prolog", "foo :- bar."))
        assert not verdict.ok
        assert not client.posts, "no engine should have been asked"

    def test_an_empty_query_is_refused(self):
        verdict = _run(studio_validation.validate_query(
            _Client(), "http://api", "cypher", "   "))
        assert not verdict.ok

    def test_an_unreachable_engine_does_not_condemn_the_query(self):
        verdict = _run(studio_validation.validate_query(
            _Client(boom=OSError("connection refused")),
            "http://api", "cypher", "MATCH (c) RETURN c"))
        assert verdict.ok, "an outage must not be reported as a bad query"
        assert not verdict.checked
        assert verdict.warnings

    @pytest.mark.parametrize("status", [502, 503, 504])
    def test_a_timeout_or_outage_is_a_warning_not_a_rejection(self, status):
        verdict = _run(studio_validation.validate_query(
            _Client({"cypher": _Resp(status, {"detail": "timeout"})}),
            "http://api", "cypher", "MATCH (c) RETURN c"))
        assert verdict.ok and not verdict.checked

    def test_a_non_json_error_body_still_produces_a_message(self):
        verdict = _run(studio_validation.validate_query(
            _Client({"sql": _Resp(400, None, text="syntax error at or near")}),
            "http://api", "sql", "SELEC 1"))
        assert not verdict.ok
        assert "syntax error" in verdict.errors[0]

    def test_sparql_columns_come_back_for_the_plot_checker(self):
        verdict = _run(studio_validation.validate_query(
            _Client({"sparql": _Resp(200, {"head": {"vars": ["a", "b"]}})}),
            "http://api", "sparql", "SELECT ?a ?b WHERE { ?a ?p ?b }"))
        assert verdict.columns == ["a", "b"]


# ── how a plot is checked ──────────────────────────────────────


def _spec(**kw):
    base = {"chart": "bar_h",
            "sources": [{"name": "s1", "lang": "cypher",
                         "query": "MATCH (c) RETURN c.name AS company"}],
            "x": "company"}
    base.update(kw)
    return base


class TestPlotValidation:

    def test_a_good_spec_passes(self):
        client = _Client({"cypher": _Resp(200, {"columns": ["company"]})})
        verdict = _run(studio_validation.validate_plot(client, "http://api", _spec()))
        assert verdict.ok, verdict.errors

    def test_an_unknown_chart_type_is_named_with_the_alternatives(self):
        verdict = _run(studio_validation.validate_plot(
            _Client(), "http://api", _spec(chart="pie_3d")))
        assert not verdict.ok
        assert "pie_3d" in verdict.errors[0]
        assert "bar_h" in verdict.errors[0]

    def test_a_plot_with_no_sources_says_so(self):
        spec = _spec()
        del spec["sources"]
        verdict = _run(studio_validation.validate_plot(_Client(), "http://api", spec))
        assert not verdict.ok
        assert "sources" in verdict.errors[0]

    def test_a_source_missing_its_query_is_named(self):
        verdict = _run(studio_validation.validate_plot(
            _Client(), "http://api",
            _spec(sources=[{"name": "s1", "lang": "cypher"}])))
        assert not verdict.ok
        assert "query" in verdict.errors[0]

    def test_a_broken_source_query_blocks_the_plot(self):
        # The other half of the reported problem: a plot carries its own
        # queries, so it could smuggle in a broken one.
        client = _Client({"cypher": _Resp(400, {"detail": "Invalid input"})})
        verdict = _run(studio_validation.validate_plot(client, "http://api", _spec()))
        assert not verdict.ok
        assert "source 0" in verdict.errors[0]

    def test_an_axis_naming_a_column_that_does_not_exist_is_caught(self):
        client = _Client({"cypher": _Resp(200, {"columns": ["company"]})})
        verdict = _run(studio_validation.validate_plot(
            client, "http://api", _spec(x="comapny")))
        assert not verdict.ok
        assert "comapny" in verdict.errors[0]
        # And says what it could have meant, which is the actionable half.
        assert "company" in verdict.errors[0]

    def test_axes_are_not_second_guessed_when_a_transform_rewrites_them(self):
        # `transform` is DuckDB SQL run in the browser; it renames and
        # derives columns we cannot see. Confident wrong advice is worse
        # than silence.
        client = _Client({"cypher": _Resp(200, {"columns": ["company"]})})
        verdict = _run(studio_validation.validate_plot(
            client, "http://api",
            _spec(x="total_eur", transform="SELECT company AS total_eur FROM s1")))
        assert verdict.ok

    def test_series_and_corrcols_are_checked_too(self):
        client = _Client({"cypher": _Resp(200, {"columns": ["company"]})})
        verdict = _run(studio_validation.validate_plot(
            client, "http://api", _spec(series=["nope"])))
        assert not verdict.ok
        assert "series[0]" in verdict.errors[0]

    def test_a_spec_that_is_not_an_object_is_refused(self):
        verdict = _run(studio_validation.validate_plot(
            _Client(), "http://api", ["not", "a", "spec"]))
        assert not verdict.ok


# ── what the agent is actually told ────────────────────────────


class _FakeQuery:
    def __init__(self, qid="q-1", lang="cypher", query="MATCH (c) RETURN c"):
        self.id, self.lang, self.query = qid, lang, query
        self.name = "q"


class _FakeProject:
    def __init__(self):
        self.id, self.name = "p-1", "proj"
        self.investigation_id = None
        self.queries = [_FakeQuery()]
        self.plots = []


class _FakePlot:
    def __init__(self, plot_id="pl-1", spec=None):
        self.id, self.name, self.spec = plot_id, "p", spec or {}


class _FakeService:
    """Records writes so a test can prove one did not happen."""

    def __init__(self):
        self.writes: list[str] = []

    async def get_project(self, _user, _pid):
        return _FakeProject()

    async def add_query(self, _user, _pid, _name, lang, query):
        self.writes.append("add_query")
        return _FakeQuery("q-new", lang, query)

    async def update_query(self, _user, _pid, qid, _name, lang, query):
        self.writes.append("update_query")
        return _FakeQuery(qid, lang or "cypher", query or "")

    async def add_plot(self, _user, _pid, _name, spec):
        self.writes.append("add_plot")
        return _FakePlot("pl-1", spec)

    async def update_plot(self, _user, _pid, plot_id, _name, spec):
        self.writes.append("update_plot")
        return _FakePlot(plot_id, spec or {})


class TestTheToolRefusesRatherThanPretending:

    @staticmethod
    def _ops_and_service(client):
        svc = _FakeService()
        return StudioOps(svc, "u-1"), svc, client

    def test_a_broken_query_is_not_written(self):
        ops, svc, client = self._ops_and_service(
            _Client({"cypher": _Resp(400, {"detail": "Invalid input 'MTCH'"})}))
        out = json.loads(_run(ops.execute(
            "mcp__gmr__studio_add_query",
            {"project_id": "p-1", "name": "q", "lang": "cypher",
             "query": "MTCH (c) RETURN c"},
            client=client, api_url="http://api")))
        assert "error" in out
        assert out["valid"] is False
        assert "MTCH" in json.dumps(out["errors"])
        assert not svc.writes, "a rejected query must not reach the store"

    def test_the_refusal_tells_the_agent_to_try_again(self):
        ops, _, client = self._ops_and_service(
            _Client({"cypher": _Resp(400, {"detail": "boom"})}))
        out = json.loads(_run(ops.execute(
            "mcp__gmr__studio_add_query",
            {"project_id": "p-1", "query": "x", "lang": "cypher"},
            client=client, api_url="http://api")))
        assert "call this tool again" in out["hint"]

    def test_a_good_query_is_written_and_says_nothing_extra(self):
        ops, svc, client = self._ops_and_service(_Client())
        out = json.loads(_run(ops.execute(
            "mcp__gmr__studio_add_query",
            {"project_id": "p-1", "name": "q", "lang": "cypher",
             "query": "MATCH (c) RETURN c"},
            client=client, api_url="http://api")))
        assert svc.writes == ["add_query"]
        # No "valid: true" noise on the happy path — the model pays for
        # every token of it on every turn.
        assert "validation" not in out
        assert "error" not in out

    def test_an_unchecked_query_is_written_and_flagged(self):
        ops, svc, client = self._ops_and_service(
            _Client(boom=OSError("refused")))
        out = json.loads(_run(ops.execute(
            "mcp__gmr__studio_add_query",
            {"project_id": "p-1", "name": "q", "lang": "cypher",
             "query": "MATCH (c) RETURN c"},
            client=client, api_url="http://api")))
        assert svc.writes == ["add_query"]
        assert out["validation"]["checked"] is False

    def test_an_update_validates_against_the_stored_language(self):
        # The call changes only the text; the language it will run under is
        # the one already saved. Validating against the default would check
        # something nobody will execute.
        client = _Client()
        ops, _, _ = self._ops_and_service(client)
        _run(ops.execute("mcp__gmr__studio_update_query",
                         {"project_id": "p-1", "query_id": "q-1",
                          "query": "MATCH (c) RETURN c"},
                         client=client, api_url="http://api"))
        assert client.posts[0][0].endswith("/query/cypher")

    def test_a_broken_plot_is_not_written(self):
        ops, svc, client = self._ops_and_service(_Client())
        out = json.loads(_run(ops.execute(
            "mcp__gmr__studio_add_plot",
            {"project_id": "p-1", "name": "p", "spec": {"chart": "pie_3d"}},
            client=client, api_url="http://api")))
        assert "error" in out
        assert not svc.writes

    def test_without_a_client_nothing_is_validated_and_writes_proceed(self):
        # Every existing caller that has no fontem-api to ask behaves as it
        # always did, rather than losing the ability to write.
        ops, svc, _ = self._ops_and_service(None)
        out = json.loads(_run(ops.execute(
            "mcp__gmr__studio_add_query",
            {"project_id": "p-1", "name": "q", "lang": "cypher",
             "query": "MTCH broken"})))
        assert svc.writes == ["add_query"]
        assert "error" not in out
