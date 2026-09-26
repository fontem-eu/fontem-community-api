"""Proposing new text for the query open in the Data Studio editor.

The Studio verbs write straight through to the saved project. That was
fine while the assistant could not see the editor; now that the turn
carries the query the user has OPEN — with a draft the server has never
seen — a write to it would land under their cursor unannounced. So the
open query gets a proposal instead: the model sends the whole new text,
the engine checks it, and the editor shows a diff the user accepts or
rejects as a whole.

What these pin, in the order it fails in practice:

- The tool is offered only when a query is open, at both surface tiers.
- Every refusal is a sentence the model can act on, and nothing is ever
  written on the way to one.
- A query the engine rejects is withdrawn with the engine's words; one
  the engine could not check is proposed with a warning, like add_query.
- update_query is turned away from the open query's TEXT and nothing
  else — names, languages and other queries behave as before.
- The status event carries the proposal the editor draws the diff from.
"""
# pylint: disable=missing-function-docstring,protected-access
# pylint: disable=redefined-outer-name,dangerous-default-value
# -- `json=` is the keyword validate_query calls the engine with; EDITOR
#    is a constant the helpers read and never write.
from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

from src.assistant import pydantic_ai_client as pai
from src.assistant import studio_context, studio_tools
from src.assistant.engine_tools import turn_tool_specs
from src.assistant.studio_ops import StudioOps
from src.assistant.tool_runtime import ToolRuntime
from src.services.data_project_service import MAX_QUERY_CHARS

PROPOSE = studio_tools.PROPOSE_QUERY_TOOL_NAME
UPDATE = "mcp__gmr__studio_update_query"
ROUTES = [{"path": "/map", "description": "Atlas"}]
EDITOR = {"project_id": "p-1", "query_id": "q-1", "lang": "cypher"}
GOOD = {"project_id": "p-1", "query_id": "q-1",
        "query": "MATCH (c:Company) RETURN count(c) AS n",
        "explanation": "Counts the companies instead of listing them."}


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _names(specs):
    return [s["function"]["name"] for s in specs]


class _Resp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class _Engine:
    """The query proxy as validate_query sees it: one POST, one answer."""

    def __init__(self, resp=None, boom=None):
        self.posts = []
        self._resp = resp or _Resp(200, {"columns": ["n"], "rows": []})
        self._boom = boom

    async def post(self, url, json=None, timeout=None):  # noqa: A002
        del timeout
        self.posts.append((url, json))
        if self._boom:
            raise self._boom
        return self._resp


class _Recorder:
    """A DataProjectService that only remembers being written to."""

    def __init__(self):
        self.calls = []

    # pylint: disable-next=too-many-arguments,too-many-positional-arguments
    async def update_query(self, _user, _project, query_id, name, lang, query):
        self.calls.append(("update_query", query_id, name, lang, query))
        q = MagicMock()
        q.id, q.name, q.lang, q.query = query_id, name or "kept", lang or "cypher", query or "kept"
        return q


# pylint: disable-next=too-many-arguments
def _dispatch(rt, name, args, *, studio_editor=None, client=None, studio=None):
    return _run(rt.dispatch(
        client, name, args, studio=studio, nav_routes=[], pending_nav=[],
        budget=[14_000], name_cache={}, traced=[],
        studio_editor=studio_editor,
    ))


def _propose(args, *, studio_editor=EDITOR, client=None, studio=None):
    out, _ = _dispatch(ToolRuntime(gmr_api_url="http://fontem-api"),
                       PROPOSE, args, studio_editor=studio_editor,
                       client=client, studio=studio)
    return json.loads(out)


# ── offered only with a query open ────────────────────────────

def test_the_tool_is_offered_only_when_a_query_is_open():
    for compact in (False, True):
        for has_editor in (False, True):
            without = _names(turn_tool_specs([], has_editor, ROUTES,
                                             compact=compact))
            with_query = _names(turn_tool_specs(
                [], has_editor, ROUTES, compact=compact, studio_editor=True))
            assert PROPOSE not in without, (compact, has_editor)
            assert PROPOSE in with_query, (compact, has_editor)
            # It is the only thing the editor adds — the document surface
            # is has_editor's business, not this flag's.
            assert set(with_query) - set(without) == {PROPOSE}


def test_it_follows_the_studio_verbs_it_supersedes():
    got = _names(turn_tool_specs([], True, ROUTES, studio_editor=True))
    assert got.index(PROPOSE) > got.index(UPDATE)


def test_the_tool_is_a_proposal_not_a_studio_write():
    # STUDIO_ACTIONS is what StudioOps executes server-side. This one must
    # never be dispatched there: it has no operation and writes nothing.
    assert PROPOSE not in studio_tools.STUDIO_ACTIONS
    assert PROPOSE not in StudioOps.OPS
    params = studio_tools.PROPOSE_QUERY_TOOL["function"]["parameters"]
    assert params["required"] == ["project_id", "query_id", "query", "explanation"]


# ── refusals ───────────────────────────────────────────────────

def test_without_an_open_query_it_says_so():
    out = _propose(GOOD, studio_editor=None)
    assert out["error"].startswith("no query is open in the Data Studio editor")


def test_only_the_open_query_can_be_proposed():
    out = _propose({**GOOD, "query_id": "q-2"})
    assert out["error"].startswith("only the open query can be proposed:")
    out = _propose({**GOOD, "project_id": "p-9"})
    assert out["error"].startswith("only the open query can be proposed:")


def test_an_empty_query_is_refused():
    assert _propose({**GOOD, "query": "  \n"})["error"] == "the query is empty"


def test_an_over_long_query_is_refused_at_the_length_the_save_would_be():
    out = _propose({**GOOD, "query": "x" * (MAX_QUERY_CHARS + 1)})
    assert out["error"] == f"the query is too long (max {MAX_QUERY_CHARS} characters)"
    assert "error" not in _propose({**GOOD, "query": "x" * MAX_QUERY_CHARS})


def test_an_explanation_is_required():
    out = _propose({**GOOD, "explanation": " "})
    assert out["error"] == "explanation is required"


# ── the engine's verdict ───────────────────────────────────────

def test_a_query_the_engine_rejects_is_withdrawn_with_its_words():
    engine = _Engine(_Resp(400, {"detail": "Invalid input 'RETRN'"}))
    recorder = _Recorder()
    out = _propose(GOOD, client=engine, studio=StudioOps(recorder, "u-1"))
    assert out["error"].startswith(
        "the proposal was withdrawn because the query does not work: ")
    assert "RETRN" in out["error"]
    assert "proposed" not in out
    # Nothing was written on the way to the refusal.
    assert not recorder.calls
    # And the engine was asked in the OPEN query's language, with a plan
    # rather than a run.
    url, body = engine.posts[0]
    assert url.endswith("/query/cypher")
    assert body["query"].startswith("EXPLAIN ")


def test_an_unreachable_engine_proposes_with_a_warning():
    # Failing open on infrastructure, like add_query: an outage must not
    # read to the model as a fault in a query that may well be fine.
    out = _propose(GOOD, client=_Engine(boom=ConnectionError("down")))
    assert out["proposed"] is True
    assert out["action"] == "propose_query"
    assert out["warnings"] and "could not reach" in out["warnings"][0]


def test_a_query_the_engine_accepts_is_proposed_with_its_columns():
    out = _propose(GOOD, client=_Engine(_Resp(200, {"columns": ["n"], "rows": []})))
    # The pacing note rides on every dispatched result and is not the
    # subject here.
    out.pop("turn_status", None)
    assert out == {
        "proposed": True, "action": "propose_query",
        "project_id": "p-1", "query_id": "q-1", "lang": "cypher",
        "columns": ["n"], "warnings": [],
    }


# ── the store is the assistant's choice ────────────────────────
#
# The user asks in words. Whether the answer lives in the Neo4j graph or
# in Virtuoso (legislation, Wikidata) is for the assistant to decide, so a
# proposal may change the open query's language as well as its text — and
# it is checked by the engine it names, not the one the editor had.

def test_a_proposal_may_switch_the_store_and_is_checked_by_that_engine():
    engine = _Engine(_Resp(200, {"head": {"vars": ["act"]}, "results": {"bindings": []}}))
    out = _propose({**GOOD, "lang": "sparql",
                    "query": "SELECT ?act WHERE { ?act a ?t } LIMIT 5"}, client=engine)
    assert out["proposed"] is True
    assert out["lang"] == "sparql"
    assert len(engine.posts) == 1
    url, sent = engine.posts[0]
    assert url.endswith("/sparql")
    # SPARQL has no EXPLAIN: it is sent as written.
    assert sent["query"].startswith("SELECT ?act")


def test_without_a_language_the_open_query_keeps_its_own():
    engine = _Engine()
    out = _propose(GOOD, client=engine)
    assert out["lang"] == "cypher"
    assert len(engine.posts) == 1
    url, sent = engine.posts[0]
    assert url.endswith("/query/cypher") and sent["query"].startswith("EXPLAIN ")


def test_an_unknown_language_is_refused_before_any_engine_is_asked():
    engine = _Engine()
    out = _propose({**GOOD, "lang": "gremlin"}, client=engine)
    assert out["error"].startswith("unknown query language 'gremlin'")
    assert not engine.posts


def test_the_tool_offers_every_store():
    params = studio_tools.PROPOSE_QUERY_TOOL["function"]["parameters"]
    assert params["properties"]["lang"]["enum"] == ["cypher", "sql", "sparql"]
    # Optional: omitting it must keep working for a model that does not care.
    assert "lang" not in params["required"]


def test_the_prompt_says_the_store_is_the_assistants_choice():
    block = studio_context.system_context({"project_id": "p", "project_name": "P", "query": {
        "id": "q", "name": "n", "lang": "cypher", "text": "MATCH (n) RETURN n"}})
    assert "Pass `lang`" in block and "Virtuoso" in block


def test_without_an_engine_it_is_proposed_unchecked_and_says_so():
    out = _propose(GOOD)
    assert out["proposed"] is True and out["warnings"]


# ── update_query keeps its hands off the open text ─────────────

def _update(args, *, studio_editor=EDITOR):
    recorder = _Recorder()
    out, _ = _dispatch(ToolRuntime(gmr_api_url="http://fontem-api"),
                       UPDATE, args, studio_editor=studio_editor,
                       studio=StudioOps(recorder, "u-1"))
    return json.loads(out), recorder


def test_rewriting_the_open_query_text_is_refused_and_not_written():
    out, recorder = _update({"project_id": "p-1", "query_id": "q-1",
                             "query": "MATCH (n) RETURN n"})
    assert out["error"].startswith(
        "this query is open in the user's editor; propose the change with "
        "mcp__gmr__studio_propose_query")
    assert not recorder.calls


def test_renaming_the_open_query_still_goes_through():
    out, recorder = _update({"project_id": "p-1", "query_id": "q-1",
                             "name": "Companies by count"})
    assert "error" not in out
    assert recorder.calls == [("update_query", "q-1", "Companies by count",
                               None, None)]


def test_another_query_is_updated_as_before():
    out, recorder = _update({"project_id": "p-1", "query_id": "q-2",
                             "query": "MATCH (n) RETURN n"})
    assert "error" not in out
    assert recorder.calls[0][1] == "q-2"


def test_with_no_query_open_update_query_is_unguarded():
    out, recorder = _update({"project_id": "p-1", "query_id": "q-1",
                             "query": "MATCH (n) RETURN n"},
                            studio_editor=None)
    assert "error" not in out
    assert len(recorder.calls) == 1


# ── the status event the editor draws from ─────────────────────

def test_the_status_event_carries_the_proposal():
    class Part:
        tool_name = PROPOSE
        args = json.dumps(GOOD)

    class Ev:
        part = Part()

    out = pai.PydanticAIProxyClient._tool_status(Ev(), {}, 0.0)
    assert out.startswith("event: status\n")
    data = json.loads(out.split("data: ", 1)[1])
    assert data["phase"] == "tool_use"
    assert data["tool"] == PROPOSE
    assert data["proposal"] == {**GOOD, "action": "propose_query"}
    # Labelled like the other proposal verbs, not left as the raw name.
    assert data["detail"].startswith("Proposing a query")
    assert "studio_action" not in data


# ── what the model is told ─────────────────────────────────────

def _studio(**query):
    base = {"id": "q-1", "name": "Companies", "lang": "cypher",
            "text": "MATCH (c:Company) RETURN c.name LIMIT 5",
            "last_error": None, "columns": None}
    base.update(query)
    return {"project_id": "p-1", "project_name": "Procurement", "query": base}


def test_the_prompt_section_names_the_open_query():
    block = studio_context.system_context(_studio())
    assert block.startswith("## Data Studio\n")
    assert 'project "Procurement" (project_id: p-1)' in block
    assert 'Open query: "Companies" (query_id: q-1), language: cypher.' in block
    assert "Current text:\n```\nMATCH (c:Company) RETURN c.name LIMIT 5\n```" in block
    assert "mcp__gmr__studio_propose_query" in block
    assert "Last run error" not in block
    assert "Result columns" not in block


def test_the_last_error_and_columns_appear_when_present():
    block = studio_context.system_context(_studio(
        last_error="Variable `x` not defined", columns=["name", "n"]))
    assert "Last run error: Variable `x` not defined" in block
    assert "Result columns: name, n" in block


def test_a_long_query_is_capped_with_a_marker():
    block = studio_context.system_context(_studio(text="x" * 5000))
    assert "x" * studio_context.MAX_QUERY_CHARS + " …[truncated]" in block
    assert "x" * (studio_context.MAX_QUERY_CHARS + 1) not in block


def test_without_an_open_query_only_the_project_is_described():
    block = studio_context.system_context(
        {"project_id": "p-1", "project_name": "Procurement", "query": None})
    assert 'project "Procurement" (project_id: p-1)' in block
    assert block.rstrip().endswith("No query is open.")
    assert "propose_query" not in block


def test_outside_the_studio_there_is_no_section():
    assert studio_context.system_context(None) == ""


def test_open_query_reads_both_shapes():
    assert studio_context.open_query(_studio()) == EDITOR
    assert studio_context.open_query(EDITOR) == EDITOR
    assert studio_context.open_query(
        {"project_id": "p-1", "project_name": "P", "query": None}) is None
    assert studio_context.open_query(None) is None


# ── both engines thread the editor through ─────────────────────
#
# The tests above call dispatch directly. `doc` was once threaded by the
# service and dropped by the engine, and read_document answered "no
# document is open" on every real turn while these stayed green. So the
# editor is checked at the engine boundary too: a payload with one offers
# the verb to the agent, and the verb it offers knows which query is open.

PAYLOAD = {"message": "count them", "system": "s", "local_model_id": "qwen3-4b",
           "studio_editor": EDITOR}


class _Ctx:
    """`async with agent.run_stream_events(...)` over no events at all."""

    async def __aenter__(self):
        async def nothing():
            return
            yield  # pylint: disable=unreachable
        return nothing()

    async def __aexit__(self, *_):
        return False


class _FakeTool:
    def __init__(self, function, name, **_):
        self.function, self.name = function, name

    @classmethod
    def from_schema(cls, function, name, **kw):
        return cls(function, name, **kw)


def _pydantic_engine(monkeypatch):
    built = []

    class _Agent:
        def __init__(self, _model, system_prompt="", tools=(), **_):
            del system_prompt
            self.tools = list(tools)
            built.append(self)

        @staticmethod
        def run_stream_events(_message):
            return _Ctx()

    monkeypatch.setattr(pai, "_import_pydantic_ai", lambda: (
        _Agent, MagicMock(), MagicMock(), _FakeTool, MagicMock()))

    async def no_generated(*_a, **_k):
        return []
    monkeypatch.setattr(pai.generated_tools, "fetch_tools", no_generated)
    return pai.PydanticAIProxyClient(gmr_api_url="http://fontem-api",
                                     local_url="http://llm"), built


async def _drain(stream):
    return [ev async for ev in stream]


def test_the_pydantic_engine_offers_the_verb_to_the_open_query(monkeypatch):
    engine, built = _pydantic_engine(monkeypatch)
    _run(_drain(engine.stream(PAYLOAD)))
    tool = next(t for t in built[0].tools if t.name == PROPOSE)
    # Asked for the wrong query: the refusal names the one that IS open,
    # which only the editor bound to this turn could have told it.
    out = json.loads(_run(tool.function(**{**GOOD, "query_id": "q-2"})))
    assert "'q-1'" in out["error"]


def test_the_pydantic_engine_withholds_the_verb_without_an_open_query(monkeypatch):
    engine, built = _pydantic_engine(monkeypatch)
    _run(_drain(engine.stream({k: v for k, v in PAYLOAD.items()
                               if k != "studio_editor"})))
    assert PROPOSE not in [t.name for t in built[0].tools]


def _langgraph_engine(monkeypatch):
    # pylint: disable-next=import-outside-toplevel
    from src.assistant import langgraph_client as lg
    built = []

    class _Agent:
        def __init__(self, tools):
            self.tools = list(tools)
            built.append(self)

        @staticmethod
        async def astream(*_a, **_k):
            return
            yield  # pylint: disable=unreachable

    def create_agent(model=None, tools=(), system_prompt=""):
        del model, system_prompt
        return _Agent(tools)

    def structured_tool(name, description, args_schema, func, coroutine):
        del description, args_schema, func
        return type("T", (), {"name": name, "coroutine": staticmethod(coroutine)})

    monkeypatch.setattr(lg, "_import_langchain",
                        lambda: (create_agent, structured_tool, MagicMock()))

    async def no_generated(*_a, **_k):
        return []
    monkeypatch.setattr(lg.generated_tools, "fetch_tools", no_generated)
    return lg.LangGraphProxyClient(gmr_api_url="http://fontem-api",
                                   local_url="http://llm"), built


def test_the_langgraph_engine_offers_the_verb_to_the_open_query(monkeypatch):
    engine, built = _langgraph_engine(monkeypatch)
    _run(_drain(engine.stream(PAYLOAD)))
    tool = next(t for t in built[0].tools if t.name == PROPOSE)
    out = json.loads(_run(tool.coroutine(**{**GOOD, "query_id": "q-2"})))
    assert "'q-1'" in out["error"]


def test_the_langgraph_engine_withholds_the_verb_without_an_open_query(monkeypatch):
    engine, built = _langgraph_engine(monkeypatch)
    _run(_drain(engine.stream({k: v for k, v in PAYLOAD.items()
                               if k != "studio_editor"})))
    assert PROPOSE not in [t.name for t in built[0].tools]
