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
    # So swapping qwen3-4b-q4_k_m for something else does not invalidate
    # every stored preference.
    for m in LOCAL_MODELS:
        assert "." not in m.id
        assert "q4" not in m.id
        assert m.id != m.served_name


def test_a_known_id_resolves_to_its_served_name():
    fast = resolve("fast")
    assert fast.served_name == "qwen3-1.7b-q4_k_m"


def test_an_unknown_id_falls_back_to_the_default():
    # A preference can outlive the option it names.
    assert resolve("gpt-5").id == DEFAULT_MODEL_ID
    assert resolve(None).id == DEFAULT_MODEL_ID
    assert resolve("").id == DEFAULT_MODEL_ID


def test_resolution_is_case_and_space_insensitive():
    assert resolve("  FAST ").id == "fast"


def test_a_path_traversal_id_cannot_reach_the_server():
    # The whole reason ids are curated rather than passed through.
    for hostile in ["../../etc/passwd", "/models/secret.gguf", "qwen3-4b-q4_k_m"]:
        assert not is_known(hostile)
        assert resolve(hostile).id == DEFAULT_MODEL_ID


def test_the_offered_list_carries_a_speed_so_faster_is_a_number():
    offered = as_dicts()
    assert [m["id"] for m in offered] == [m.id for m in LOCAL_MODELS]
    assert all(m["tokens_per_second"] > 0 for m in offered)


def test_the_fast_option_is_actually_faster_than_the_default():
    fast = resolve("fast")
    assert fast.tokens_per_second > resolve(DEFAULT_MODEL_ID).tokens_per_second


def test_no_secret_material_is_exposed_in_the_offered_list():
    # This endpoint is unauthenticated-adjacent; it must carry nothing but
    # the choice itself.
    for m in as_dicts():
        assert set(m) == {"id", "tokens_per_second"}


def test_the_offered_list_says_nothing_about_whether_it_applies():
    # `active` is computed per request from the caller's credentials, not
    # baked into the catalogue — the same list is right for everyone, the
    # applicability is not.
    for m in as_dicts():
        assert "active" not in m
