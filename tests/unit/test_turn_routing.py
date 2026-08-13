"""Which endpoint a turn is sent to.

This is the piece worth testing directly: it decides whether a request
carries a user's secret to a third party or stays inside the cluster.
Getting it wrong is not a visible bug, it is a leak.

Ported from test_local_llm_routing.py when the hand-written executor was
decommissioned. The decision moved out of that loop into `resolve_route`,
shared by both remaining executors — the assertions did not change, because
the property being protected did not.
"""
import pytest

from src.assistant.local_models import DEFAULT_MODEL_ID, resolve
from src.assistant.tool_runtime import LOCAL_PROVIDER, resolve_route

LOCAL = "http://llama-server.llm-service.svc.cluster.local:8080"


def _route(cred=None, *, local_url=LOCAL, local_model_id=None,
           default_model="mistral-small-latest"):
    route, err = resolve_route(
        cred, local_url=local_url, local_model_id=local_model_id,
        default_model=default_model,
    )
    return route, err


def test_no_credential_uses_the_built_in_model():
    # The default path for almost every user. Before this existed they got
    # "assistant unavailable" and that was the end of the feature.
    route, err = _route({})
    assert err == ""
    assert route.base_url.startswith(LOCAL)
    assert route.local is True
    # The name llama-server's router knows it by, resolved from the curated
    # default id rather than taken from the caller.
    assert route.model == resolve(DEFAULT_MODEL_ID).served_name


def test_built_in_is_never_sent_an_authorization_header():
    route, _ = _route({})
    assert route.api_key == "", "a cluster-local server must not be handed a secret"


def test_a_missing_credential_object_is_the_same_as_none_supplied():
    route, err = _route(None)
    assert err == ""
    assert route.local is True
    assert route.api_key == ""


def test_user_key_goes_to_the_hosted_provider_not_the_local_one():
    route, err = _route(
        {"provider": "mistral", "api_key": "sk-user", "model": "mistral-large-latest"}
    )
    assert err == ""
    assert route.base_url == "https://api.mistral.ai/v1"
    assert route.api_key == "sk-user"
    assert route.model == "mistral-large-latest"
    assert route.local is False


def test_an_openai_key_goes_to_openai():
    # The decommissioned loop sent every provider's key to Mistral's URL, so
    # an OpenAI key could only ever come back 401.
    route, err = _route({"provider": "openai", "api_key": "sk-user"})
    assert err == ""
    assert route.base_url == "https://api.openai.com/v1"
    assert route.api_key == "sk-user"


def test_an_unsupported_provider_is_refused_rather_than_guessed():
    # Anthropic is offered in the UI but does not speak this protocol.
    # Sending the key anyway is how it used to 401; saying so is better.
    route, err = _route({"provider": "anthropic", "api_key": "sk-ant"})
    assert route is None
    assert "anthropic" in err
    assert "not supported" in err


def test_a_refused_provider_does_not_leak_the_key_into_the_message():
    _, err = _route({"provider": "anthropic", "api_key": "sk-secret-value"})
    assert "sk-secret-value" not in err


def test_explicitly_choosing_the_built_in_ignores_a_supplied_key():
    # Belt and braces: if a client ever posts provider=local with a key
    # attached, the key must not travel to the local server.
    route, _ = _route({"provider": LOCAL_PROVIDER, "api_key": "sk-user"})
    assert route.base_url.startswith(LOCAL)
    assert route.api_key == ""


def test_built_in_ignores_a_caller_supplied_model():
    # A caller naming a model directly must not reach llama-server: the
    # choice is a curated id, and anything else resolves to the default.
    route, _ = _route({"provider": LOCAL_PROVIDER, "model": "gpt-4o"})
    assert route.model == resolve(DEFAULT_MODEL_ID).served_name


def test_a_chosen_model_id_selects_that_model():
    route, _ = _route({}, local_model_id="fast")
    assert route.model == resolve("fast").served_name


def test_an_unoffered_model_id_falls_back_rather_than_failing():
    # A preference can outlive the option it names. Falling back beats
    # refusing to answer because of a stale row.
    route, _ = _route({}, local_model_id="definitely-not-a-model")
    assert route.model == resolve(DEFAULT_MODEL_ID).served_name


def test_falls_back_to_a_user_key_when_no_local_server_is_configured():
    route, err = _route({"provider": "mistral", "api_key": "sk-user"}, local_url="")
    assert err == ""
    assert route.base_url == "https://api.mistral.ai/v1"
    assert route.api_key == "sk-user"


def test_no_local_server_and_no_key_at_all_is_unroutable():
    route, err = _route({}, local_url="")
    assert route is None
    assert err, "the caller needs something to render"


def test_local_gets_a_longer_timeout_than_a_hosted_provider():
    # CPU generation runs at single-digit tokens/sec; the hosted timeout
    # would abort a turn that was progressing normally.
    local, _ = _route({})
    hosted, _ = _route({"provider": "mistral", "api_key": "sk-user"})
    assert local.timeout > hosted.timeout


@pytest.mark.parametrize("provider", ["LOCAL", " local ", "Local"])
def test_provider_matching_is_case_and_space_insensitive(provider):
    route, _ = _route({"provider": provider, "api_key": "sk-x"})
    assert route.base_url.startswith(LOCAL)
    assert route.api_key == ""


@pytest.mark.parametrize("provider", ["MISTRAL", " Mistral "])
def test_hosted_provider_matching_is_also_forgiving(provider):
    route, err = _route({"provider": provider, "api_key": "sk-x"})
    assert err == ""
    assert route.base_url == "https://api.mistral.ai/v1"


def test_a_provider_without_a_key_uses_the_built_in_model():
    # A half-configured credential row must not become an unroutable turn.
    route, err = _route({"provider": "mistral", "api_key": ""})
    assert err == ""
    assert route.local is True
