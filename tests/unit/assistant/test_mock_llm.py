"""The scripted model the assistant e2e tests drive.

Three things need to hold, and they fail in different ways:

* The script picks the right next call from what has already come back —
  including deriving `investigate_entity`'s id from what `search_entities`
  actually returned, which is what makes an e2e built on it still able to
  catch a broken tool.
* The bytes on the wire are what an OpenAI client expects. This is checked
  by running PydanticAI itself against the endpoint over an in-process ASGI
  transport, because "looks like the OpenAI format" is exactly the kind of
  belief that survives review and fails on deploy.
* It does not exist unless switched on. The code ships in the production
  image, so the gate is the whole safety argument and gets its own tests.
"""
# pylint: disable=protected-access,redefined-outer-name
from __future__ import annotations

import json
import os

import httpx
import pytest

from src.assistant import mock_llm

pytestmark = pytest.mark.usefixtures("mock_enabled")


@pytest.fixture
def mock_enabled(monkeypatch):
    monkeypatch.setenv("ASSIST_MOCK_MODEL", "true")
    yield


def _search_result(entities) -> str:
    return json.dumps(entities)


def _history(*pairs) -> list[dict]:
    """Build an OpenAI-shaped history from (tool name, result) pairs."""
    msgs: list[dict] = [{"role": "user",
                         "content": "E2E-SCENARIO: toolchain please count"}]
    for i, (name, result) in enumerate(pairs):
        call_id = f"c{i}"
        msgs.append({"role": "assistant", "content": None, "tool_calls": [
            {"id": call_id, "type": "function", "function": {"name": name}}]})
        msgs.append({"role": "tool", "tool_call_id": call_id, "name": name,
                     "content": result})
    return msgs


# ── the script ─────────────────────────────────────────────────


class TestTheScriptedChain:

    def test_it_searches_first(self):
        step = mock_llm.next_step(_history())
        assert step["tool"] == "mcp__gmr__search_entities"
        assert step["args"]["query"]

    def test_it_investigates_the_id_the_search_returned(self):
        # The point of the whole design: the id is not baked into the
        # script, it comes out of the real tool's real answer. Change what
        # search returns and this call follows it.
        msgs = _history(("mcp__gmr__search_entities",
                         _search_result([{"gmr_id": "abc-123"}])))
        step = mock_llm.next_step(msgs)
        assert step["tool"] == "mcp__gmr__investigate_entity"
        assert step["args"]["entity_id"] == "abc-123"

    def test_it_reads_the_shape_search_entities_actually_returns(self):
        # Recorded from fontem-testing. The first version of this guessed
        # "results"/"entities" and the e2e failed on the real payload —
        # search_entities returns one list PER ENTITY TYPE, not one list.
        real = {
            "query": "Siemens AG",
            "companies": [{"gmr_id": "b559559e-6158-5868-a28c-90b4805bc7f0",
                           "name": "Siemens AG", "country": "DE",
                           "ticker": "SIE", "exchange": "XETR",
                           "currency": "EUR", "is_active": True,
                           "symbol": "SIE.DE"}],
            "authorities": [], "persons": [], "lobbyists": [],
        }
        msgs = _history(("mcp__gmr__search_entities", json.dumps(real)))
        step = mock_llm.next_step(msgs)
        assert step["args"]["entity_id"] == "b559559e-6158-5868-a28c-90b4805bc7f0"

    def test_it_falls_back_to_an_authority_when_no_company_matched(self):
        payload = {"query": "Metro", "companies": [],
                   "authorities": [{"authority_id": "a-4", "name": "Metro"}]}
        msgs = _history(("mcp__gmr__search_entities", json.dumps(payload)))
        assert mock_llm.next_step(msgs)["args"]["entity_id"] == "a-4"

    @pytest.mark.parametrize("payload,expected", [
        ([{"gmr_id": "g-1"}], "g-1"),
        ({"results": [{"entity_id": "e-2"}]}, "e-2"),
        ({"entities": [{"id": "i-3"}]}, "i-3"),
        ([{"authority_id": "a-4"}], "a-4"),
        # A key nobody has seen: still better to find the id than to fail.
        ({"somethingNew": [{"gmr_id": "n-5"}]}, "n-5"),
    ])
    def test_it_tolerates_other_shapes_too(self, payload, expected):
        msgs = _history(("mcp__gmr__search_entities", json.dumps(payload)))
        assert mock_llm.next_step(msgs)["args"]["entity_id"] == expected

    def test_all_lists_empty_is_still_a_visible_failure(self):
        payload = {"query": "x", "companies": [], "authorities": [],
                   "persons": [], "lobbyists": []}
        msgs = _history(("mcp__gmr__search_entities", json.dumps(payload)))
        assert mock_llm.next_step(msgs)["text"].startswith("MOCK-FAIL")

    def test_an_empty_search_becomes_a_visible_failure(self):
        # Never invent an id. A test reading this text fails with the reason
        # attached, which is the opposite of what the 1.7B did.
        msgs = _history(("mcp__gmr__search_entities", _search_result([])))
        step = mock_llm.next_step(msgs)
        assert "tool" not in step
        assert step["text"].startswith("MOCK-FAIL")

    def test_unparseable_search_output_also_fails_loudly(self):
        msgs = _history(("mcp__gmr__search_entities", "<html>nope</html>"))
        assert mock_llm.next_step(msgs)["text"].startswith("MOCK-FAIL")

    def test_it_reads_a_doc_then_navigates_then_answers(self):
        msgs = _history(
            ("mcp__gmr__search_entities", _search_result([{"gmr_id": "x-1"}])),
            ("mcp__gmr__investigate_entity",
             json.dumps({"props": {"contract_count": 7}})),
        )
        assert mock_llm.next_step(msgs)["tool"] == "get_doc"

        msgs = _history(
            ("mcp__gmr__search_entities", _search_result([{"gmr_id": "x-1"}])),
            ("mcp__gmr__investigate_entity",
             json.dumps({"props": {"contract_count": 7}})),
            ("get_doc", json.dumps({"body": "..."})),
        )
        step = mock_llm.next_step(msgs)
        assert step["tool"] == "navigate"
        assert step["args"]["path"] == "/companies"

    def test_the_final_answer_carries_the_count_the_tool_reported(self):
        msgs = _history(
            ("mcp__gmr__search_entities", _search_result([{"gmr_id": "x-1"}])),
            ("mcp__gmr__investigate_entity",
             json.dumps({"props": {"contract_count": 7}})),
            ("get_doc", json.dumps({"body": "..."})),
            ("navigate", json.dumps({"ok": True})),
        )
        step = mock_llm.next_step(msgs)
        assert "tool" not in step
        assert "7" in step["text"]
        assert "x-1" in step["text"]

    def test_a_missing_count_fails_rather_than_rounding_to_zero(self):
        msgs = _history(
            ("mcp__gmr__search_entities", _search_result([{"gmr_id": "x-1"}])),
            ("mcp__gmr__investigate_entity", json.dumps({"props": {}})),
        )
        assert mock_llm.next_step(msgs)["text"].startswith("MOCK-FAIL")

    def test_a_count_of_zero_is_still_an_answer(self):
        # Zero contracts is a real finding, not a missing one.
        msgs = _history(
            ("mcp__gmr__search_entities", _search_result([{"gmr_id": "x-1"}])),
            ("mcp__gmr__investigate_entity",
             json.dumps({"props": {"contract_count": 0}})),
            ("get_doc", "{}"), ("navigate", "{}"),
        )
        step = mock_llm.next_step(msgs)
        assert "0" in step["text"] and "MOCK-FAIL" not in step["text"]

    def test_an_unknown_scenario_does_nothing(self):
        step = mock_llm.next_step([{"role": "user", "content": "hello there"}])
        assert "tool" not in step

    def test_the_echo_scenario_answers_without_tools(self):
        step = mock_llm.next_step(
            [{"role": "user", "content": "E2E-SCENARIO: echo ping"}])
        assert "tool" not in step
        assert "MOCK-OK" in step["text"]


# ── the gate ───────────────────────────────────────────────────


class TestItDoesNotExistUnlessSwitchedOn:

    def test_enabled_follows_the_env(self, monkeypatch):
        for value in ("1", "true", "TRUE", "yes", "on"):
            monkeypatch.setenv("ASSIST_MOCK_MODEL", value)
            assert mock_llm.enabled()
        for value in ("", "0", "false", "no", "off"):
            monkeypatch.setenv("ASSIST_MOCK_MODEL", value)
            assert not mock_llm.enabled()

    def test_the_routes_are_absent_in_a_production_shaped_app(self, monkeypatch):
        # The whole safety argument for shipping this in the prod image.
        monkeypatch.delenv("ASSIST_MOCK_MODEL", raising=False)
        # pylint: disable-next=import-outside-toplevel
        from fastapi.testclient import TestClient
        # pylint: disable-next=import-outside-toplevel
        from src.api.app import build_app
        with TestClient(build_app()) as client:
            resp = client.post("/mock-llm/v1/chat/completions", json={})
            assert resp.status_code == 404

    def test_the_id_is_not_selectable_when_disabled(self, monkeypatch):
        # pylint: disable-next=import-outside-toplevel
        from src.assistant import local_models
        monkeypatch.setenv("ASSIST_MOCK_MODEL", "true")
        assert local_models.is_known(mock_llm.MOCK_MODEL_ID)
        monkeypatch.delenv("ASSIST_MOCK_MODEL", raising=False)
        assert not local_models.is_known(mock_llm.MOCK_MODEL_ID)

    def test_it_is_never_in_the_model_picker(self, monkeypatch):
        # Offered to nobody, in any environment: it is a test fixture, not a
        # product choice, and a user who picked it would get a script.
        # pylint: disable-next=import-outside-toplevel
        from src.assistant import local_models
        monkeypatch.setenv("ASSIST_MOCK_MODEL", "true")
        assert mock_llm.MOCK_MODEL_ID not in [
            m["id"] for m in local_models.as_dicts()]


# ── the wire format, against a real client ─────────────────────


@pytest.fixture
def mock_app(monkeypatch):
    monkeypatch.setenv("ASSIST_MOCK_MODEL", "true")
    # pylint: disable-next=import-outside-toplevel
    from src.api.app import build_app
    return build_app()


class TestPydanticAiCanActuallyReadIt:
    """Runs PydanticAI against the endpoint over an in-process ASGI
    transport. No ports, no llama-server — but a real OpenAI client parsing
    real bytes, which is the only way to know the chunks are right before
    they are deployed."""

    @staticmethod
    def _agent(mock_app, tools):
        pytest.importorskip("pydantic_ai")
        # pylint: disable=import-outside-toplevel
        from pydantic_ai import Agent
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.openai import OpenAIProvider

        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=mock_app),
            base_url="http://mock/mock-llm/v1",
        )
        model = OpenAIChatModel(
            mock_llm.MOCK_MODEL_ID,
            provider=OpenAIProvider(base_url="http://mock/mock-llm/v1",
                                    api_key="none", http_client=client),
        )
        return Agent(model, system_prompt="test", tools=tools)

    @pytest.mark.anyio
    async def test_a_text_only_turn_streams_back(self, mock_app):
        agent = self._agent(mock_app, [])
        result = await agent.run("E2E-SCENARIO: echo hello")
        assert "MOCK-OK" in str(result.output)

    @pytest.mark.anyio
    async def test_a_tool_call_is_parsed_and_executed(self, mock_app):
        pytest.importorskip("pydantic_ai")
        # pylint: disable-next=import-outside-toplevel
        from pydantic_ai import Tool

        seen: list[dict] = []

        async def search(query: str = "", limit: int = 5) -> str:
            seen.append({"query": query, "limit": limit})
            return json.dumps([{"gmr_id": "wire-1"}])

        tool = Tool.from_schema(
            function=search, name="mcp__gmr__search_entities",
            description="search", json_schema={
                "type": "object",
                "properties": {"query": {"type": "string"},
                               "limit": {"type": "integer"}}},
        )
        agent = self._agent(mock_app, [tool])
        await agent.run("E2E-SCENARIO: toolchain count them")
        assert seen, "PydanticAI did not parse the tool call off the wire"
        assert seen[0]["query"] == "Siemens AG"


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ── routing ────────────────────────────────────────────────────


class TestRouting:

    @staticmethod
    def _route(**kw):
        # pylint: disable-next=import-outside-toplevel
        from src.assistant.tool_runtime import resolve_route
        base = {"local_url": "http://llama:8080", "default_model": "d",
                "local_model_id": mock_llm.MOCK_MODEL_ID}
        base.update(kw)
        return resolve_route(None, **base)

    def test_the_mock_id_goes_to_the_mock_url(self):
        route, err = self._route(mock_url="http://api/mock-llm")
        assert err == ""
        assert route.base_url == "http://api/mock-llm/v1"
        assert route.model == mock_llm.MOCK_MODEL_ID

    def test_without_a_url_it_falls_through_to_the_real_models(self):
        # A half-configured environment must not send a turn to a dead
        # address; it gets the default model instead.
        route, _ = self._route(mock_url="")
        assert route.base_url == "http://llama:8080/v1"
        assert route.model != mock_llm.MOCK_MODEL_ID

    def test_it_never_routes_when_the_flag_is_off(self, monkeypatch):
        monkeypatch.delenv("ASSIST_MOCK_MODEL", raising=False)
        route, _ = self._route(mock_url="http://api/mock-llm")
        assert route.base_url == "http://llama:8080/v1"

    def test_a_caller_key_still_wins_nothing_from_the_mock(self):
        # A turn spending someone's own key must go to their provider, not
        # to a script, even if the stored model id is the mock's.
        # pylint: disable-next=import-outside-toplevel
        from src.assistant.tool_runtime import resolve_route
        route, _ = resolve_route(
            {"provider": "openai", "api_key": "sk-x", "model": "gpt-4o"},
            local_url="http://llama:8080", local_model_id=mock_llm.MOCK_MODEL_ID,
            default_model="d", mock_url="http://api/mock-llm")
        assert "mock-llm" not in route.base_url
        assert route.api_key == "sk-x"


def test_the_env_default_is_off():
    # Belt and braces on the default, read from the process the suite runs
    # in rather than from the source.
    assert os.environ.get("ASSIST_MOCK_MODEL", "") != "" or not mock_llm.enabled()
