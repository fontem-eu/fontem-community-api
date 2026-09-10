"""The HTTP executor that fronts the fontem-api query proxies."""
# pylint: disable=missing-function-docstring,redefined-outer-name
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from src.services.query_executor import HttpQueryExecutor


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture()
def stub(monkeypatch):
    """Route the executor's httpx client at a handler, and record requests."""
    seen: list[dict] = []
    original = httpx.AsyncClient

    def install(handler):
        transport = httpx.MockTransport(lambda request: _record(request, handler, seen))

        def _client(*args, **kwargs):
            kwargs["transport"] = transport
            return original(*args, **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", _client)
        return seen

    return install


def _record(request, handler, seen):
    body = json.loads(request.content) if request.content else {}
    seen.append({"url": str(request.url), "json": body})
    return handler(request)


def _ok(payload=None):
    return lambda _request: httpx.Response(200, json=payload or {
        "columns": ["item_id"], "rows": [["a"]], "row_count": 1, "truncated": False,
    })


def test_it_posts_to_the_engines_own_path_and_forwards_params(stub):
    seen = stub(_ok())
    result = _run(HttpQueryExecutor(base_url="http://fontem-api")
                  .run("sql", "SELECT 1", {"nuts": ["PT"]}))

    assert seen[0]["url"] == "http://fontem-api/query/sql"
    assert seen[0]["json"] == {"query": "SELECT 1", "params": {"nuts": ["PT"]}}
    assert result.columns == ["item_id"]
    assert result.row_count == 1
    assert result.error is None


def test_empty_params_are_omitted_entirely(stub):
    """psycopg switches into interpolation mode the moment a mapping arrives,
    which changes how a literal '%' is treated."""
    seen = stub(_ok())
    _run(HttpQueryExecutor(base_url="http://fontem-api").run("sql", "SELECT 1", {}))
    assert "params" not in seen[0]["json"]


def test_each_engine_routes_to_its_own_endpoint(stub):
    seen = stub(_ok())
    executor = HttpQueryExecutor(base_url="http://fontem-api")
    _run(executor.run("cypher", "MATCH (n) RETURN n"))
    _run(executor.run("sparql", "SELECT * WHERE {}"))
    assert [s["url"] for s in seen] == [
        "http://fontem-api/query/cypher",
        "http://fontem-api/sparql",
    ]


def test_the_proxys_own_message_is_surfaced(stub):
    """The proxy explains *why* a query was rejected — that explanation is the
    whole value of showing it to the author."""
    stub(lambda _r: httpx.Response(400, json={
        "detail": "SQL: write/DDL keyword 'DELETE' is not allowed (read-only studio)."
    }))
    result = _run(HttpQueryExecutor(base_url="http://fontem-api").run("sql", "DELETE FROM t"))
    assert "DELETE" in result.error
    assert result.rows == []


def test_an_unreachable_proxy_is_an_error_not_an_exception(stub):
    def _boom(_request):
        raise httpx.ConnectError("connection refused")

    stub(_boom)
    result = _run(HttpQueryExecutor(base_url="http://fontem-api").run("sql", "SELECT 1"))
    assert "could not reach the query proxy" in result.error


def test_an_unknown_engine_is_rejected_without_a_request(stub):
    seen = stub(_ok())
    result = _run(HttpQueryExecutor(base_url="http://fontem-api").run("mongo", "db.find()"))
    assert "unsupported engine" in result.error
    assert not seen


def test_a_non_json_response_is_reported_plainly(stub):
    stub(lambda _r: httpx.Response(200, text="<html>gateway</html>"))
    result = _run(HttpQueryExecutor(base_url="http://fontem-api").run("sql", "SELECT 1"))
    assert "non-JSON" in result.error


# ── outage vs. bad query ──────────────────────────────────────
#
# A failed run means two very different things, and the catalogue acts
# destructively on one of them: validate_query DEMOTES a published query
# that stops validating. So "the store was down" must never arrive
# looking like "this query is wrong".


def test_an_unreachable_proxy_is_flagged_as_a_store_outage(stub):
    def handler(_request):
        raise httpx.ConnectError("connection refused")

    stub(handler)
    res = _run(HttpQueryExecutor("http://api").run("cypher", "MATCH (n) RETURN n"))
    assert res.store_unreachable is True
    assert "could not reach the query proxy" in res.error


@pytest.mark.parametrize("status", [502, 503, 504])
def test_gateway_statuses_are_store_outages(stub, status):
    """502/503/504 are the proxy saying the store behind it is gone or
    too slow. fontem-api answers 503 for a Neo4j
    ServiceUnavailable/SessionExpired; before that it answered 400 and an
    outage was indistinguishable from a malformed query."""
    stub(lambda _r: httpx.Response(status, json={"detail": "store down"}))
    res = _run(HttpQueryExecutor("http://api").run("cypher", "MATCH (n) RETURN n"))
    assert res.store_unreachable is True


@pytest.mark.parametrize("status", [400, 422])
def test_client_errors_are_not_store_outages(stub, status):
    """The carve-out must stay narrow, or a genuinely broken query would
    stay published forever."""
    stub(lambda _r: httpx.Response(status, json={"detail": "Invalid input 'RETRUN'"}))
    res = _run(HttpQueryExecutor("http://api").run("cypher", "MATCH (n) RETRUN n"))
    assert res.store_unreachable is False
    assert "RETRUN" in res.error


def test_a_successful_run_is_not_an_outage(stub):
    stub(lambda _r: httpx.Response(200, json={"columns": ["n"], "rows": [[1]]}))
    res = _run(HttpQueryExecutor("http://api").run("cypher", "MATCH (n) RETURN n"))
    assert res.store_unreachable is False
    assert res.error is None
