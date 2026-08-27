"""The probe: a thin pass-through or a hole. These pin "thin".

Protection parity is not re-implemented here — it is inherited by going
through the same proxies as the Run button. What the tests own is that the
probe goes THROUGH those proxies (right path per language, nothing added,
nothing stripped), that refusal detail survives — the model fixes queries
from "write keyword 'MERGE' is not allowed", not from a status code — and
that results ride the shared budget cap like every other tool result.
"""
# pylint: disable=missing-function-docstring,protected-access
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

from src.assistant import probe_tools
from src.assistant.tool_runtime import ToolRuntime


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _client(status=200, text='{"rows": [[42]], "columns": ["n"]}'):
    client = MagicMock()
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    client.post = AsyncMock(return_value=resp)
    return client


def _dispatch(args, client, budget=14_000):
    rt = ToolRuntime(gmr_api_url="http://fontem-api")
    b = [budget]
    out, _ = _run(rt.dispatch(
        client, probe_tools.PROBE_TOOL_NAME, args,
        studio=None, nav_routes=[], pending_nav=[],
        budget=b, name_cache={}, traced=[],
    ))
    return out


def test_each_language_reaches_its_own_proxy():
    for lang, path in (("cypher", "/query/cypher"), ("sql", "/query/sql"),
                       ("sparql", "/sparql")):
        client = _client()
        out = _dispatch({"lang": lang, "query": "MATCH (n) RETURN count(n)"},
                        client)
        assert client.post.await_args.args[0] == f"http://fontem-api{path}"
        assert '"rows"' in out


def test_the_query_travels_verbatim():
    # Rewriting a caller's query is how surprises get built; the proxy is
    # the validator, not us.
    client = _client()
    q = "MATCH (c:Company {country:'RUS'}) RETURN count(c)"
    _dispatch({"lang": "cypher", "query": q}, client)
    assert client.post.await_args.kwargs["json"] == {"query": q}


def test_a_proxy_refusal_carries_its_detail():
    client = _client(status=400,
                     text="cypher: write/DDL keyword 'MERGE' is not allowed")
    out = json.loads(_dispatch({"lang": "cypher", "query": "MERGE (n)"},
                               client))
    assert "refused" in out["error"]
    assert "MERGE" in out["detail"]


def test_an_unknown_language_is_refused_with_the_menu():
    out = json.loads(_dispatch({"lang": "graphql", "query": "x"}, _client()))
    assert "unknown lang" in out["error"]
    assert "cypher" in out["hint"]


def test_an_empty_query_is_refused():
    out = json.loads(_dispatch({"lang": "cypher", "query": "  "}, _client()))
    assert out["error"] == "query is required"


def test_a_fat_result_rides_the_shared_budget_cap():
    client = _client(text="x" * 20_000)
    out = _dispatch({"lang": "cypher", "query": "MATCH (n) RETURN n"},
                    client, budget=5_000)
    assert len(out) < 6_000
    assert "truncated" in out


def test_a_dead_engine_is_an_answer_not_an_exception():
    import httpx
    client = MagicMock()
    client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
    out = json.loads(_dispatch({"lang": "sql", "query": "SELECT 1"}, client))
    assert "did not answer" in out["error"]
