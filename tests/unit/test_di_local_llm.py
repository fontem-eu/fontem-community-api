"""The DI provider must hand the proxy client its local-server URL.

This is the gap that shipped: routing was correct and unit-tested, the
chart set LOCAL_LLM_URL, and the assistant still called Mistral — because
the DI provider builds the executor directly and never passed the
argument. Every test constructed the client itself, so nothing noticed.
"""
# pylint: disable=protected-access
# Reaching into the constructed client is the point: the bug was that a
# constructor argument never arrived, which is invisible from outside.
import os
from unittest.mock import patch

from src.api.di import AssistantProvider
from src.assistant.tool_runtime import resolve_route


def _build():
    # Call the underlying function; @provide wraps it for dishka.
    provider = AssistantProvider()
    factory = getattr(AssistantProvider.proxy_client, "__wrapped__", None)
    return (factory or AssistantProvider.proxy_client.__call__)(provider)


def test_local_url_reaches_the_client():
    env = {"LLM_PROVIDER": "mistral", "LOCAL_LLM_URL": "http://llama-server:8080"}
    with patch.dict(os.environ, env, clear=False):
        client = _build()
    assert client._local_url == "http://llama-server:8080"


def test_local_model_reaches_the_client():
    env = {
        "LLM_PROVIDER": "mistral",
        "LOCAL_LLM_URL": "http://llama-server:8080",
        "LOCAL_LLM_MODEL": "qwen3-8b",
    }
    with patch.dict(os.environ, env, clear=False):
        client = _build()
    assert client._local_model == "qwen3-8b"


def test_a_keyless_turn_routes_local_not_to_the_platform_key():
    # The actual regression, end to end through construction: a platform
    # Mistral key is present, the user has none, and the turn must still
    # go to the local server rather than spending the platform key.
    env = {
        "LLM_PROVIDER": "mistral",
        "MISTRAL_API_KEY": "sk-platform",
        "LOCAL_LLM_URL": "http://llama-server:8080",
    }
    with patch.dict(os.environ, env, clear=False):
        client = _build()
    route, err = resolve_route(
        {}, local_url=client._local_url, local_model_id=None,
        default_model="unused",
    )
    assert err == ""
    assert route.base_url.startswith("http://llama-server:8080")
    assert route.api_key == ""


def test_without_a_local_url_a_keyless_turn_fails_loudly():
    """The platform key is no longer a silent fallback.

    The decommissioned executor ended with `if self._api_key: use it`, so a
    missing LOCAL_LLM_URL quietly spent the platform Mistral key on every
    user — which is the same failure this module was written about, only
    from the other direction: nothing visible went wrong, the bill just
    moved. An error the caller can render is the better answer, and the
    only turns that legitimately leave the cluster are the ones carrying a
    key their owner supplied.
    """
    env = {"LLM_PROVIDER": "mistral", "MISTRAL_API_KEY": "sk-platform"}
    with patch.dict(os.environ, env, clear=False):
        os.environ.pop("LOCAL_LLM_URL", None)
        client = _build()
    route, err = resolve_route(
        {}, local_url=client._local_url, local_model_id=None,
        default_model="unused",
    )
    assert route is None
    assert err
    assert "sk-platform" not in err


def test_the_default_executor_is_the_one_production_runs():
    """ASSISTANT_ENGINE unset must not mean "some third thing".

    It used to select the hand-written loop. That loop is gone, and the
    default is now the executor production has run since 2026-08-12 and the
    only one the e2e battery exercises.
    """
    env = {"LLM_PROVIDER": "mistral", "LOCAL_LLM_URL": "http://llama-server:8080"}
    with patch.dict(os.environ, env, clear=False):
        os.environ.pop("ASSISTANT_ENGINE", None)
        client = _build()
    assert type(client).__name__ == "PydanticAIProxyClient"


def test_langgraph_is_the_one_opt_in():
    env = {
        "LLM_PROVIDER": "mistral",
        "LOCAL_LLM_URL": "http://llama-server:8080",
        "ASSISTANT_ENGINE": "langgraph",
    }
    with patch.dict(os.environ, env, clear=False):
        client = _build()
    assert type(client).__name__ == "LangGraphProxyClient"
