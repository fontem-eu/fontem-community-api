"""The language directive exists because tool output changed the answer's language.

Measured on the eval fixture: qwen3-1.7b and qwen3-4b both scored -100% on
P09 — a French question answered in English, every run. The tool schemas and
the JSON that comes back are English, so once a search returns, the model's
best guess at "what language is this conversation" flips.
"""
import pytest

from src.assistant.mistral_client import _language_directive


def test_names_the_language_in_its_own_language():
    """"Answer in français" cues a multilingual model better than "French"."""
    out = _language_directive("fr")
    assert "français" in out
    assert "French" not in out


def test_english_needs_no_directive():
    """The default costs tokens in every turn and buys nothing."""
    assert _language_directive("en") == ""


def test_unknown_or_missing_locale_is_silent():
    """Never guess. A wrong language instruction is worse than none."""
    assert _language_directive(None) == ""
    assert _language_directive("") == ""
    assert _language_directive("klingon") == ""


@pytest.mark.parametrize("locale", ["pt-PT", "pt-BR", "PT"])
def test_region_and_case_variants_resolve_to_the_language(locale):
    assert "português" in _language_directive(locale)


def test_directive_names_tool_output_as_the_trap():
    """The instruction has to address the actual failure, not just say
    'answer in X' — the model already believed it was doing that."""
    out = _language_directive("de").lower()
    assert "tool" in out


def test_identifiers_are_exempted():
    """Translating a ticker or an entity name would corrupt the citation."""
    out = _language_directive("es").lower()
    assert "names" in out or "identifiers" in out


def test_every_supported_locale_has_an_endonym():
    """A locale the platform serves but the directive cannot name would
    silently fall back to no instruction — the bug this fixes."""
    from src.assistant.mistral_client import _LOCALE_NAMES
    # 24 EU official languages, which is what the UI ships.
    assert len(_LOCALE_NAMES) == 24
    assert all(v and v != k for k, v in _LOCALE_NAMES.items())
