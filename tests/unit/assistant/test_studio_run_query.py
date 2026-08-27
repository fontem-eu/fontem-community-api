"""studio_run_query: the verb that closes the loop.

The model could create and update queries but never see what they returned
— it wrote six queries in the motivating sessions, two of them schema
probes, and read back nothing from any of them. These pin the new verb:
execution through the same guarded proxy as the Run button, results capped
with a marker, and every failure mode expressed as something the model can
act on.
"""
# pylint: disable=missing-function-docstring,protected-access
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

from src.assistant.studio_ops import StudioOps, _RESULT_CHARS


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _query(qid="q-1", lang="cypher", text="MATCH (n) RETURN n LIMIT 5"):
    q = MagicMock()
    q.id, q.lang, q.query, q.name = qid, lang, text, "probe"
    return q


def _ops(queries=()):
    svc = MagicMock()
    project = MagicMock()
    project.queries = list(queries)
    svc.get_project = AsyncMock(return_value=project)
    return StudioOps(svc, "u-1")


def _client(status=200, text='{"rows": [[1]], "columns": ["n"]}'):
    client = MagicMock()
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    client.post = AsyncMock(return_value=resp)
    return client


def _exec(ops, args, client):
    return json.loads(_run(ops.execute(
        "mcp__gmr__studio_run_query", args,
        client=client, api_url="http://fontem-api")))


def test_a_saved_query_runs_through_the_guarded_proxy():
    client = _client()
    out = _exec(_ops([_query()]), {"project_id": "p-1", "query_id": "q-1"},
                client)
    assert out["lang"] == "cypher"
    assert '"rows"' in out["result"]
    url = client.post.await_args.args[0]
    assert url == "http://fontem-api/query/cypher", \
        "must be the same proxy as the Run button — no second engine"


def test_the_language_picks_the_proxy():
    client = _client()
    _exec(_ops([_query(lang="sparql")]),
          {"project_id": "p-1", "query_id": "q-1"}, client)
    assert client.post.await_args.args[0].endswith("/sparql")


def test_an_unknown_query_id_names_the_fix():
    out = _exec(_ops([_query("other")]),
                {"project_id": "p-1", "query_id": "q-9"}, _client())
    assert "no query" in out["error"]
    assert "studio_get_project" in out["hint"]


def test_a_fat_result_is_truncated_with_a_marker():
    client = _client(text="x" * (_RESULT_CHARS + 5_000))
    out = _exec(_ops([_query()]), {"project_id": "p-1", "query_id": "q-1"},
                client)
    assert len(out["result"]) < _RESULT_CHARS + 200
    assert "truncated" in out["result"]


def test_an_engine_rejection_carries_the_detail():
    client = _client(status=400, text="syntax error at MATCH")
    out = _exec(_ops([_query()]), {"project_id": "p-1", "query_id": "q-1"},
                client)
    assert "rejected" in out["error"]
    assert "syntax error" in out["detail"]


def test_no_client_leaves_the_query_intact_and_says_so():
    out = json.loads(_run(_ops([_query()]).execute(
        "mcp__gmr__studio_run_query",
        {"project_id": "p-1", "query_id": "q-1"})))
    assert "no query engine" in out["error"]
    assert "intact" in out["hint"]
