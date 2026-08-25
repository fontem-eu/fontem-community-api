"""The two Nebius models the platform pays for.

`resolve_route` decides whether a turn leaves the cluster and whose key pays
for it. These models are the first built-ins that cost money per turn, so the
questions worth pinning are: does a user's own key still win, does an
unconfigured environment degrade instead of failing, and can a hosted model's
provider name ever reach llama-server.
"""
from __future__ import annotations

import importlib

import pytest

from src.assistant import local_models, tool_runtime


@pytest.fixture(name="with_key")
def _with_key(monkeypatch):
    monkeypatch.setenv("NEBIUS_API_KEY", "platform-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-key")
    importlib.reload(local_models)
    yield
    importlib.reload(local_models)


@pytest.fixture(name="without_key")
def _without_key(monkeypatch):
    monkeypatch.delenv("NEBIUS_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    importlib.reload(local_models)
    yield
    importlib.reload(local_models)


# --- what the picker offers -------------------------------------------------

def test_hosted_models_are_hidden_without_a_key(without_key):  # pylint: disable=unused-argument
    offered = [m["id"] for m in local_models.as_dicts()]
    assert "gpt-oss-120b" not in offered
    assert "qwen3-30b" not in offered
    assert "qwen3-4b" in offered


def test_hosted_models_are_offered_with_a_key(with_key):  # pylint: disable=unused-argument
    offered = [m["id"] for m in local_models.as_dicts()]
    assert "gpt-oss-120b" in offered
    assert "qwen3-30b" in offered


def test_labels_name_the_provider(with_key):  # pylint: disable=unused-argument
    labels = {m["id"]: m["label"] for m in local_models.as_dicts()}
    assert labels["gpt-oss-120b"] == "GPT-OSS 120B [nebius]"
    assert labels["qwen3-30b"] == "Qwen3 30B A3B [nebius]"


def test_hosted_flag_is_exposed_so_the_ui_need_not_parse_the_label(with_key):  # pylint: disable=unused-argument
    by_id = {m["id"]: m for m in local_models.as_dicts()}
    assert by_id["gpt-oss-120b"]["hosted"] is True
    assert by_id["qwen3-4b"]["hosted"] is False


def test_an_unconfigured_hosted_id_cannot_be_stored(without_key):  # pylint: disable=unused-argument
    assert local_models.is_known("gpt-oss-120b") is False


def test_a_configured_hosted_id_can_be_stored(with_key):  # pylint: disable=unused-argument
    assert local_models.is_known("gpt-oss-120b") is True


# --- where the turn actually goes -------------------------------------------

def route(model_id, cred=None):
    return tool_runtime.resolve_route(
        cred, local_url="http://llama:8080",
        local_model_id=model_id, default_model="fallback")


def test_a_hosted_choice_goes_to_the_provider_on_the_platform_key(with_key):  # pylint: disable=unused-argument
    r, err = route("gpt-oss-120b")
    assert not err
    assert r.base_url == local_models.NEBIUS_BASE_URL
    assert r.model == "openai/gpt-oss-120b"
    assert r.api_key == "platform-key"
    assert r.local is False


def test_the_users_own_key_still_wins(with_key):  # pylint: disable=unused-argument
    """A caller spending their own provider key must keep spending it, even
    with a hosted built-in stored as their preference. Otherwise we would
    quietly move their turn onto our bill — and onto a provider they did not
    choose."""
    r, err = route("gpt-oss-120b",
                   cred={"provider": "openai", "api_key": "theirs",
                         "model": "gpt-4o"})
    assert not err
    assert r.api_key == "theirs"
    assert r.model == "gpt-4o"
    assert r.base_url == tool_runtime.PROVIDER_BASE_URLS["openai"]


def test_a_hosted_id_without_a_key_degrades_to_the_default(without_key):  # pylint: disable=unused-argument
    """The provider's model name must never reach llama-server: it has never
    heard of "openai/gpt-oss-120b" and would fail every turn. A preference
    outliving its key degrades to the default instead."""
    r, err = route("gpt-oss-120b")
    assert not err
    assert r.local is True
    assert r.api_key == ""
    assert r.model == local_models.resolve(local_models.DEFAULT_MODEL_ID).served_name
    assert "/" not in r.model


def test_a_local_choice_is_unaffected(with_key):  # pylint: disable=unused-argument
    r, err = route("qwen3-8b")
    assert r.local is True
    assert r.api_key == ""
    assert r.model == "qwen3-8b-q4_k_m"


def test_the_cluster_local_server_is_never_handed_the_platform_key(with_key):  # pylint: disable=unused-argument
    for model_id in ("qwen3-1.7b", "qwen3-4b", "qwen3-8b"):
        r, _ = route(model_id)
        assert r.api_key == "", f"{model_id} was routed with a key attached"


def test_anonymous_visitors_stay_on_the_smallest_local_model(with_key):  # pylint: disable=unused-argument
    """Anonymous turns are unmetered and unauthenticated. They must not be
    able to spend platform money by any route."""
    anon = local_models.resolve(local_models.ANONYMOUS_MODEL_ID)
    assert anon.hosted is False
    r, _ = route(local_models.ANONYMOUS_MODEL_ID)
    assert r.local is True and r.api_key == ""


# --- the anonymous guard ----------------------------------------------------

def test_anonymous_model_id_is_the_local_1_7b(with_key):  # pylint: disable=unused-argument
    assert local_models.anonymous_model_id() == "qwen3-1.7b"
    assert local_models.resolve("qwen3-1.7b").hosted is False


def test_anonymous_refuses_a_hosted_constant(monkeypatch, with_key):  # pylint: disable=unused-argument
    """If ANONYMOUS_MODEL_ID is ever pointed at a model the platform pays for,
    the accessor must not honour it. Anonymous turns carry no account and no
    metering, so a hosted model there is an open tab for anyone who can reach
    the endpoint."""
    monkeypatch.setattr(local_models, "ANONYMOUS_MODEL_ID", "gpt-oss-120b")
    got = local_models.anonymous_model_id()
    assert got == "qwen3-1.7b"
    assert local_models.resolve(got).hosted is False


def test_anonymous_survives_a_constant_naming_nothing(monkeypatch, with_key):  # pylint: disable=unused-argument
    monkeypatch.setattr(local_models, "ANONYMOUS_MODEL_ID", "retired-model")
    assert local_models.resolve(local_models.anonymous_model_id()).hosted is False


# --- more than one hosted provider ------------------------------------------

def test_each_provider_gets_its_own_endpoint_and_key(with_key):  # pylint: disable=unused-argument
    """The bug this prevents is the one the old hand-written loop had: every
    provider's key sent to one provider's URL. A key posted to the wrong host
    is a leak, not a 401."""
    neb, _ = route("glm-5.1")
    ora, _ = route("ox-alpha")
    assert neb.base_url == local_models.HOSTED_PROVIDERS["nebius"]["base_url"]
    assert neb.api_key == "platform-key"
    assert ora.base_url == local_models.HOSTED_PROVIDERS["openrouter"]["base_url"]
    assert ora.api_key == "openrouter-key"
    assert neb.api_key != ora.api_key


def test_one_provider_configured_does_not_offer_the_other(monkeypatch):
    """A deployment with only a Nebius key must not advertise OpenRouter
    models — picking one would fail every turn."""
    monkeypatch.setenv("NEBIUS_API_KEY", "platform-key")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    importlib.reload(local_models)
    try:
        offered = [m["id"] for m in local_models.as_dicts()]
        assert "glm-5.1" in offered
        assert "ox-alpha" not in offered
    finally:
        importlib.reload(local_models)


def test_a_model_naming_an_unknown_provider_falls_back_to_local(with_key, monkeypatch):  # pylint: disable=unused-argument
    """Rather than being sent to whichever provider is first in the table."""
    ghost = local_models.LocalModel(
        id="ghost", label="Ghost", served_name="ghost/model",
        provider="not-a-provider", tokens_per_second=1, context_tokens=1)
    monkeypatch.setitem(local_models._BY_ID, "ghost", ghost)  # pylint: disable=protected-access
    r, err = route("ghost")
    assert not err
    assert r.local is True
    assert r.api_key == ""
    assert "/" not in r.model


def test_every_offered_hosted_model_has_a_reachable_provider(with_key):  # pylint: disable=unused-argument
    for m in local_models.offered():
        if m.hosted:
            assert local_models.hosted_base_url(m.provider), m.id
            assert local_models.hosted_key(m.provider), m.id
