"""The navigate tool has to move the browser, in every executor.

Reported from production: the assistant said it had navigated and the page
did not move. `navigate` is the one tool that cannot run on the server —
the model's tool result is only a receipt, and the browser moves solely
because a `navigate` SSE event reaches it.

`navigation.navigate_result` returns both halves, `(result, emit)`, and its
own tests pass. But two of the three executors wrote

    result, _emit = navigation.navigate_result(...)
    return result

throwing the emit away. The model was told `{"ok": true, "navigated_to":
"/map"}` and said so; nothing was ever sent to the panel. The native loop
emits correctly, and testing/staging run the native loop, so the e2e gate
could not see it — production runs pydantic-ai.

So these tests are per-executor on purpose. A test of navigate_result would
have passed throughout the outage.
"""
import asyncio
import pathlib

from src.assistant import langgraph_client as lg
from src.assistant import navigation
from src.assistant import pydantic_ai_client as pai

ROUTES = [
    {"path": "/map", "description": "Atlas"},
    {"path": "/c/:id/summary", "description": "Company summary"},
]
NAV = navigation.NAVIGATE_TOOL_NAME


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _FakeTool:
    """Stands in for pydantic_ai.Tool; keeps the wrapped callable reachable."""

    def __init__(self, function=None, name="", description="", json_schema=None):
        self.function = function
        self.name = name
        self.description = description
        self.json_schema = json_schema

    @classmethod
    def from_schema(cls, *, function, name, description, json_schema):
        return cls(function=function, name=name, description=description,
                   json_schema=json_schema)


def _pai_navigate(path):
    """Invoke the pydantic-ai navigate closure; return (result, pending)."""
    client = pai.PydanticAIProxyClient(gmr_api_url="http://fake")
    specs = [{"function": {"name": NAV, "description": "d", "parameters": {}}}]
    pending: list = []
    tools = client._build_tools(  # pylint: disable=protected-access
        None, _FakeTool, specs, ROUTES, [10_000], None, pending, {},
    )
    result = _run(tools[0].function(path=path))
    return result, pending


def test_pydantic_ai_queues_a_navigate_event_for_a_valid_path():
    result, pending = _pai_navigate("/map")
    assert '"ok": true' in result.lower()
    assert pending == [{"path": "/map"}], (
        "the emit was dropped: the model is told it navigated and the "
        "browser is never told anything"
    )


def test_pydantic_ai_queues_nothing_for_a_rejected_path():
    result, pending = _pai_navigate("/does-not-exist")
    assert '"ok": false' in result.lower()
    assert not pending, "a bad path must not move anybody's screen"


def test_pydantic_ai_drains_the_queue_as_sse():
    _, pending = _pai_navigate("/map")
    out = pai.drain_navigations(pending)
    assert any("event: navigate" in line for line in out)
    assert any('"/map"' in line for line in out)
    assert not pending, "draining twice would navigate twice"


class _FakeStructuredTool:
    def __init__(self, name="", description="", args_schema=None,
                 func=None, coroutine=None):
        self.name = name
        self.description = description
        self.args_schema = args_schema
        self.func = func
        self.coroutine = coroutine


def _lg_tools(pending):
    client = lg.LangGraphProxyClient(gmr_api_url="http://fake")
    specs = [{"function": {"name": NAV, "description": "d", "parameters": {}}}]
    return client._build_tools(  # pylint: disable=protected-access
        None, _FakeStructuredTool, specs, ROUTES, [], [10_000], [], None,
        pending, {},
    )


def test_langgraph_queues_a_navigate_event_from_the_async_bridge():
    pending: list = []
    tools = _lg_tools(pending)
    _run(tools[0].coroutine(path="/map"))
    assert pending == [{"path": "/map"}]


def test_langgraph_queues_a_navigate_event_from_the_sync_bridge():
    # create_agent calls tools synchronously unless they are coroutines;
    # navigate is served by both bridges, so both must emit.
    pending: list = []
    tools = _lg_tools(pending)
    tools[0].func(path="/map")
    assert pending == [{"path": "/map"}]


def test_langgraph_queues_nothing_for_a_rejected_path():
    pending: list = []
    tools = _lg_tools(pending)
    _run(tools[0].coroutine(path="/nope"))
    assert not pending


def test_langgraph_drains_the_queue_as_sse():
    pending: list = []
    tools = _lg_tools(pending)
    _run(tools[0].coroutine(path="/map"))
    out = lg.drain_navigations(pending)
    assert any("event: navigate" in line for line in out)
    assert not pending


def test_no_executor_discards_the_emit():
    """A source-level guard on the exact shape of the bug.

    The behavioural tests above are the real check; this one names the
    anti-pattern so a future re-introduction fails loudly rather than
    quietly shipping an assistant that claims to navigate.
    """
    for mod in (pai, lg):
        src = pathlib.Path(mod.__file__).read_text("utf-8")
        assert "_emit = navigation.navigate_result" not in src, (
            f"{mod.__name__} discards the navigate emit"
        )
