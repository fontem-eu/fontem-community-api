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
    importlib.reload(local_models)
    yield
    importlib.reload(local_models)


@pytest.fixture(name="without_key")
def _without_key(monkeypatch):
    monkeypatch.delenv("NEBIUS_API_KEY", raising=False)
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
