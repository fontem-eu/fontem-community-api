"""Tests for the shared NUTS code normaliser."""
from src.services.nuts import normalize_nuts


def test_normalize_valid_uppercases():
    assert normalize_nuts("pt170") == "PT170"
    assert normalize_nuts("de21") == "DE21"


def test_normalize_none_keeps_current():
    assert normalize_nuts(None, current="PT17") == "PT17"


def test_normalize_empty_clears():
    assert normalize_nuts("", current="PT17") == ""


def test_normalize_malformed_keeps_current():
    for bad in ("not a code", "1234", "P", "PT1234", "!!"):
        assert normalize_nuts(bad, current="PT17") == "PT17"
