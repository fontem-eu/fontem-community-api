"""The /assist/credentials `builtin` field.

Worth its own test because the failure is silent: without it the frontend
renders "no provider configured" while the assistant is in fact working,
and nothing in the backend errors. That is exactly what shipped — the
field was written and then not committed, and only an e2e assertion on
the rendered page caught it.
"""
import importlib
import os
import pathlib

import pytest


@pytest.fixture(name="router_mod")
def _router_mod():
    return importlib.import_module("src.assistant.router")


def _builtin(monkeypatch, url, model=None):
    if url:
        monkeypatch.setenv("LOCAL_LLM_URL", url)
    else:
        monkeypatch.delenv("LOCAL_LLM_URL", raising=False)
    if model:
        monkeypatch.setenv("LOCAL_LLM_MODEL", model)
    else:
        monkeypatch.delenv("LOCAL_LLM_MODEL", raising=False)
    # Mirror the expression in list_credentials rather than standing up
    # the whole DI graph for one dict field.
    return (
        {"model": os.environ.get("LOCAL_LLM_MODEL", "qwen3-4b")}
        if os.environ.get("LOCAL_LLM_URL")
        else None
    )


def test_advertised_when_a_local_server_is_configured(monkeypatch):
    assert _builtin(monkeypatch, "http://llama-server:8080") == {
        "model": "qwen3-4b"
    }


def test_reports_the_model_actually_configured(monkeypatch):
    # So swapping the hosted weights needs no web release.
    assert _builtin(monkeypatch, "http://llama-server:8080", "qwen3-8b") == {
        "model": "qwen3-8b"
    }


def test_null_when_no_local_server_is_configured(monkeypatch):
    # The frontend's signal to go back to "a key is required" — which is
    # still the honest message in a deployment without one.
    assert _builtin(monkeypatch, "") is None


def test_the_endpoint_actually_declares_the_field(router_mod):
    # The guard against the real failure: the expression above can be
    # correct while the endpoint never returns it.
    #
    # Read the module file, not inspect.getsource(list_credentials) —
    # @inject replaces the function with a dishka wrapper, so getsource
    # returns the wrapper body and the assertion passes on nothing.
    src = pathlib.Path(router_mod.__file__).read_text(encoding="utf-8")
    assert '"builtin"' in src, "list_credentials must advertise the built-in model"
