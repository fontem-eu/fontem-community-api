"""The key must travel with the request, never with the client.

The proxy client is an APP-scoped singleton shared by every request. If a
user's key were stored on it, the next request — a different user — would
spend it. That is the failure this file exists to prevent, and it is the
kind that produces a support ticket about somebody else's bill rather than
a stack trace.

Rewritten when the hand-written executor was decommissioned. These used to
drive a full turn through that loop and read the SSE it emitted; the same
property is now structural, and asserting it structurally is stronger: the
executors hold no key at all, and routing is a pure function of the
credential handed in for that one turn.
"""
import pathlib

import pytest

from src.assistant import langgraph_client as lg
from src.assistant import pydantic_ai_client as pai
from src.assistant.tool_runtime import resolve_route

EXECUTORS = (pai.PydanticAIProxyClient, lg.LangGraphProxyClient)
IDS = ("pydantic-ai", "langgraph")
LOCAL = "http://llama-server:8080"


@pytest.mark.parametrize("cls", EXECUTORS, ids=IDS)
def test_the_shared_client_stores_no_key(cls):
    client = cls(local_url=LOCAL, model="platform-default")
    stored = [v for v in vars(client).values() if isinstance(v, str)]
    assert not any(v.startswith("sk-") for v in stored)
    # And there is no attribute for one to land in later.
    assert not hasattr(client, "_api_key")


@pytest.mark.parametrize("mod", (pai, lg), ids=IDS)
def test_the_executor_never_reads_a_platform_key(mod):
    src = pathlib.Path(mod.__file__).read_text("utf-8")
    assert "self._api_key" not in src, (
        "a platform key on the shared client is exactly the leak this "
        "module exists to prevent"
    )


def test_two_turns_with_different_keys_do_not_bleed_into_each_other():
    a, _ = resolve_route({"provider": "mistral", "api_key": "sk-alice"},
                         local_url=LOCAL, local_model_id=None,
                         default_model="m")
    b, _ = resolve_route({"provider": "mistral", "api_key": "sk-bob"},
                         local_url=LOCAL, local_model_id=None,
                         default_model="m")
    assert a.api_key == "sk-alice"
    assert b.api_key == "sk-bob"


def test_a_keyed_turn_does_not_change_the_next_keyless_one():
    # The actual leak shape: user A pays, user B arrives with nothing, and
    # B's turn must go to the local model rather than spending A's key.
    resolve_route({"provider": "mistral", "api_key": "sk-alice"},
                  local_url=LOCAL, local_model_id=None, default_model="m")
    after, err = resolve_route({}, local_url=LOCAL, local_model_id=None,
                               default_model="m")
    assert err == ""
    assert after.local is True
    assert after.api_key == ""


def test_no_key_and_no_local_model_is_an_error_the_user_can_act_on():
    route, err = resolve_route({}, local_url="", local_model_id=None,
                               default_model="m")
    assert route is None
    assert err
