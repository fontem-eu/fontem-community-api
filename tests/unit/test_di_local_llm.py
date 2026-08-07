"""The DI provider must hand the proxy client its local-server URL.

This is the gap that shipped: _route_for was correct and unit-tested, the
chart set LOCAL_LLM_URL, and the assistant still called Mistral — because
the DI provider builds MistralProxyClient directly and never passed the
argument. Every test constructed the client itself, so nothing noticed.
"""
# pylint: disable=protected-access
# Reaching into the constructed client is the point: the bug was that a
# constructor argument never arrived, which is invisible from outside.
import os
from unittest.mock import patch

from src.api.di import AssistantProvider
from src.assistant.mistral_client import MistralProxyClient


def _build() -> MistralProxyClient:
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
    url, key, _, _ = client._route_for({})
    assert url.startswith("http://llama-server:8080")
    assert key == ""


def test_without_a_local_url_it_still_falls_back_to_the_platform_key():
    env = {"LLM_PROVIDER": "mistral", "MISTRAL_API_KEY": "sk-platform"}
    with patch.dict(os.environ, env, clear=False):
        os.environ.pop("LOCAL_LLM_URL", None)
        client = _build()
    url, key, _, _ = client._route_for({})
    assert "mistral.ai" in url
    assert key == "sk-platform"
