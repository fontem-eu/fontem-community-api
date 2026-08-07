"""Choosing between the built-in models.

The ids are ours, not llama-server's filenames. That matters twice: a
stored preference has to survive us re-quantising or renaming the weights,
and the id travels from a browser, so "serve whatever the caller names"
would let anyone load an arbitrary file out of the model directory.
"""
from src.assistant.local_models import (
    DEFAULT_MODEL_ID,
    LOCAL_MODELS,
    as_dicts,
    is_known,
    resolve,
)


def test_the_default_is_one_of_the_offered_models():
    assert is_known(DEFAULT_MODEL_ID)


def test_ids_are_not_filenames():
    # So re-quantising a model does not invalidate every stored preference.
    # "1.7b" has a dot in it legitimately, so the check is about extensions
    # and quantisation suffixes, not punctuation.
    for m in LOCAL_MODELS:
        assert not m.id.endswith(".gguf")
        assert "q4" not in m.id
        assert "_k_m" not in m.id
        assert m.id != m.served_name


def test_every_model_carries_its_real_name():
    # Labels are proper nouns served from here, not i18n keys: "Qwen3 4B"
    # is Qwen3 4B in every language, and translating it would only create
    # 24 chances to get a product name wrong.
    for m in LOCAL_MODELS:
        assert m.label
        assert m.label != m.id


def test_every_offered_model_declares_a_usable_context():
    # The field exists because a short-context model is a real thing to
    # warn about — EuroLLM-9B trains at 4096 — even though none of the
    # currently offered models is one.
    assert all(m.context_tokens >= 8192 for m in LOCAL_MODELS)


def test_only_models_that_can_use_tools_are_offered():
    # Measured, not assumed: EuroLLM-9B made 0 tool calls out of 20, so it
    # is not on this list however good its prose is. An assistant that
    # cannot look anything up answers from memory, and ungrounded claims
    # about procurement are the failure this platform exists to fight.
    assert not any("eurollm" in m.id for m in LOCAL_MODELS)


def test_a_known_id_resolves_to_its_served_name():
    assert resolve("qwen3-1.7b").served_name == "qwen3-1.7b-q4_k_m"
    assert resolve("qwen3-8b").served_name == "qwen3-8b-q4_k_m"


def test_an_unknown_id_falls_back_to_the_default():
    # A preference can outlive the option it names.
    assert resolve("gpt-5").id == DEFAULT_MODEL_ID
    assert resolve(None).id == DEFAULT_MODEL_ID
    assert resolve("").id == DEFAULT_MODEL_ID


def test_resolution_is_case_and_space_insensitive():
    assert resolve("  QWEN3-1.7B ").id == "qwen3-1.7b"


def test_a_path_traversal_id_cannot_reach_the_server():
    # The whole reason ids are curated rather than passed through.
    for hostile in ["../../etc/passwd", "/models/secret.gguf",
                    "qwen3-4b-q4_k_m", "eurollm-9b"]:
        assert not is_known(hostile)
        assert resolve(hostile).id == DEFAULT_MODEL_ID


def test_the_offered_list_carries_a_speed_so_faster_is_a_number():
    offered = as_dicts()
    assert [m["id"] for m in offered] == [m.id for m in LOCAL_MODELS]
    assert all(m["tokens_per_second"] > 0 for m in offered)


def test_the_list_is_ordered_fastest_first():
    rates = [m.tokens_per_second for m in LOCAL_MODELS]
    assert rates == sorted(rates, reverse=True)


def test_the_offered_list_carries_nothing_but_the_choice():
    # No filenames, no paths — the browser never learns what is on disk.
    for m in as_dicts():
        assert set(m) == {"id", "label", "tokens_per_second", "context_tokens", "note"}
        assert "gguf" not in str(m).lower()
        assert "/models" not in str(m)


def test_the_offered_list_says_nothing_about_whether_it_applies():
    # `active` is computed per request from the caller's credentials, not
    # baked into the catalogue — the same list is right for everyone, the
    # applicability is not.
    for m in as_dicts():
        assert "active" not in m
