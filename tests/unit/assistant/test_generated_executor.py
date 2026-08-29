"""One executor covers every annotated route, or the schema derivation is moot.

If each generated tool needed a hand-written branch, we would be back to the
drift the OpenAPI derivation exists to avoid.
"""
import json

import pytest

from src.assistant.tool_runtime import ToolRuntime


class _Resp:
    def __init__(self, status=200, text='{"ok": true}'):
        self.status_code = status
        self.text = text


class _Client:
    def __init__(self, status=200):
        self.calls = []
        self._status = status

    async def get(self, url, params=None, timeout=None):  # pylint: disable=unused-argument
        self.calls.append({"url": url, "params": params})
        return _Resp(self._status)


def _client_with(tools):
    proxy = ToolRuntime(gmr_api_url="http://api")
    proxy._generated = tools  # pylint: disable=protected-access
    return proxy


TOOLS = [
    {"function": {"name": "get_series"},
     "_route": {"method": "GET", "path": "/atlas/series", "group": "statistics",
                "core": True}},
    {"function": {"name": "company_contracts"},
     "_route": {"method": "GET", "path": "/companies/{gmr_id}/contracts",
                "group": "contracts", "core": False}},
]


@pytest.mark.asyncio
async def test_query_params_go_on_the_query_string():
    http = _Client()
    proxy = _client_with(TOOLS)
    await proxy._execute_generated(http, "get_series",  # pylint: disable=protected-access
                                   {"dataset": "demo_r_births", "start": 2020})
    assert http.calls[0]["url"] == "http://api/atlas/series"
    assert http.calls[0]["params"] == {"dataset": "demo_r_births", "start": 2020}


@pytest.mark.asyncio
async def test_path_params_are_substituted_not_appended():
    """{gmr_id} must land in the path; sending it as ?gmr_id= hits 404."""
    http = _Client()
    proxy = _client_with(TOOLS)
    await proxy._execute_generated(http, "company_contracts",  # pylint: disable=protected-access
                                   {"gmr_id": "abc-123", "limit": 5})
    assert http.calls[0]["url"] == "http://api/companies/abc-123/contracts"
    assert http.calls[0]["params"] == {"limit": 5}


@pytest.mark.asyncio
async def test_missing_path_param_is_an_error_not_a_malformed_url():
    http = _Client()
    proxy = _client_with(TOOLS)
    out = await proxy._execute_generated(http, "company_contracts", {})  # pylint: disable=protected-access
    assert "missing path parameter" in json.loads(out)["error"]
    assert not http.calls, "must not issue a request with an unfilled path"


@pytest.mark.asyncio
async def test_unknown_tool_is_reported_not_guessed():
    proxy = _client_with(TOOLS)
    out = await proxy._execute_generated(_Client(), "nope", {})  # pylint: disable=protected-access
    assert "Unknown tool" in json.loads(out)["error"]


@pytest.mark.asyncio
async def test_api_error_surfaces_as_a_tool_error():
    proxy = _client_with(TOOLS)
    out = await proxy._execute_generated(_Client(status=503), "get_series",  # pylint: disable=protected-access
                                         {"dataset": "x"})
    assert json.loads(out)["error"] == "API 503"


@pytest.mark.asyncio
async def test_execute_tool_fetches_the_generated_list_itself(monkeypatch):
    # The engines fetch their own tool specs, so nothing else fills the
    # runtime's cache; execute_tool reading the bare cache made every
    # generated tool "Unknown tool" at dispatch — the e2e's persisted rows
    # showed get_doc erroring while being served to the model.
    from src.assistant import generated_tools

    async def fake_fetch(client, api_url):  # pylint: disable=unused-argument
        return [{"function": {"name": "get_doc"},
                 "_route": {"method": "GET", "path": "/docs/{article_id}",
                            "group": "docs", "core": True}}]

    monkeypatch.setattr(generated_tools, "fetch_tools", fake_fetch)
    proxy = ToolRuntime(gmr_api_url="http://api")
    http = _Client()
    out = await proxy.execute_tool(http, "get_doc",
                                   {"article_id": "methodology"})
    assert "Unknown tool" not in out
    assert http.calls, "the generated executor should have hit the API"
