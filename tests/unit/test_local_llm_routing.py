"""Which endpoint a turn is sent to.

This is the piece worth testing directly: it decides whether a request
carries a user's secret to a third party or stays inside the cluster.
Getting it wrong is not a visible bug, it is a leak.
"""
# pylint: disable=protected-access
# _route_for is deliberately private — nothing outside the client should
# be choosing endpoints — but it is the one decision here worth asserting
# on directly, so the tests reach in rather than driving it through a
# mocked HTTP stack that would test the mock.
import pytest

from src.assistant.mistral_client import LOCAL_PROVIDER, MistralProxyClient

LOCAL = "http://llama-server.llm-service.svc.cluster.local:8080"


def _client(**kw) -> MistralProxyClient:
    return MistralProxyClient(
        api_key=kw.pop("platform_key", ""),
        api_url="https://api.mistral.ai/v1/chat/completions",
        local_url=kw.pop("local_url", LOCAL),
        **kw,
    )


def test_no_credential_uses_the_built_in_model():
    # The default path for almost every user. Before this existed they
    # got "assistant unavailable" and that was the end of the feature.
    url, key, model, _ = _client()._route_for({})
    assert url.startswith(LOCAL)
    assert key == ""
    assert model == "qwen3-4b"


def test_built_in_is_never_sent_an_authorization_header():
    _, key, _, _ = _client()._route_for({})
    assert key == "", "a cluster-local server must not be handed a secret"


def test_user_key_goes_to_the_hosted_provider_not_the_local_one():
    url, key, model, _ = _client()._route_for(
        {"provider": "mistral", "api_key": "sk-user", "model": "mistral-large-latest"}
    )
    assert url == "https://api.mistral.ai/v1/chat/completions"
    assert key == "sk-user"
    assert model == "mistral-large-latest"


def test_explicitly_choosing_the_built_in_ignores_a_supplied_key():
    # Belt and braces: if a client ever posts provider=local with a key
    # attached, the key must not travel to the local server.
    url, key, _, _ = _client()._route_for(
        {"provider": LOCAL_PROVIDER, "api_key": "sk-user"}
    )
    assert url.startswith(LOCAL)
    assert key == ""


def test_built_in_ignores_a_caller_supplied_model():
    # llama.cpp serves exactly the weights we loaded; honouring an
    # arbitrary model string would just 404 mid-turn.
    _, _, model, _ = _client()._route_for(
        {"provider": LOCAL_PROVIDER, "model": "gpt-4o"}
    )
    assert model == "qwen3-4b"


def test_falls_back_to_a_user_key_when_no_local_server_is_configured():
    url, key, _, _ = _client(local_url="")._route_for(
        {"provider": "mistral", "api_key": "sk-user"}
    )
    assert url == "https://api.mistral.ai/v1/chat/completions"
    assert key == "sk-user"


def test_no_local_server_and_no_key_at_all_is_unroutable():
    assert _client(local_url="")._route_for({}) is None


def test_local_gets_a_longer_timeout_than_a_hosted_provider():
    # CPU generation runs at single-digit tokens/sec; the hosted timeout
    # would abort a turn that was progressing normally.
    c = _client()
    _, _, _, local_timeout = c._route_for({})
    _, _, _, hosted_timeout = c._route_for(
        {"provider": "mistral", "api_key": "sk-user"}
    )
    assert local_timeout > hosted_timeout


@pytest.mark.parametrize("provider", ["LOCAL", " local ", "Local"])
def test_provider_matching_is_case_and_space_insensitive(provider):
    url, _, _, _ = _client()._route_for({"provider": provider, "api_key": "sk-x"})
    assert url.startswith(LOCAL)
