"""Labels are one line of human text, never markup.

The DAST scanner's "Cross Site Scripting Weakness (Persistent in JSON
Response)" is raised against stored values, not reflected ones: a scanner
POSTs ``<script>alert(1);</script>`` as a dossier name, and every later GET of
the list re-serves it. These tests pin both halves — that the markup is turned
away at the boundary, and that real names in real languages still get through.
"""
from __future__ import annotations

import asyncio

import pytest

from src.api.schemas.text import _label, _spec
from tests.conftest import make_headers, seed_user

# Names that exist, or plausibly could. None of these may ever be rejected:
# the platform carries 24 languages and a title is not ours to mangle.
REAL_NAMES = [
    "Budget 2026",
    "Câmara Municipal de Lisboa",
    "Spending < 2020",                      # a bare `<` is a comparison
    "5 > 3 and 2 < 4",
    "Ministerstvo pro místní rozvoj ČR",
    "Υπουργείο Ψηφιακής Διακυβέρνησης",
    "A&B — «contrato»",
    "O'Brien & Sons (Ireland) Ltd.",
    "x<y",                                  # a comparison, not a tag: nothing closes it
    "revenue<cost per region",
]

MARKUP = [
    "<script>alert(1);</script>",
    "<img src=x onerror=alert(1)>",
    "</b>",
    "<!-- comment -->",
    "<?xml version='1.0'?>",
    "name <b>bold</b>",
]


class TestLabelRule:
    @pytest.mark.parametrize("value", REAL_NAMES)
    def test_real_names_pass_untouched(self, value):
        assert _label(value) == value

    @pytest.mark.parametrize("value", MARKUP)
    def test_markup_is_rejected(self, value):
        with pytest.raises(ValueError, match="HTML or XML tags"):
            _label(value)

    def test_control_characters_are_rejected(self):
        with pytest.raises(ValueError, match="control characters"):
            _label("a\x00b")

    def test_surrounding_whitespace_is_trimmed(self):
        assert _label("  Budget  ") == "Budget"

    def test_whitespace_only_is_rejected(self):
        with pytest.raises(ValueError, match="more than whitespace"):
            _label("   ")

    def test_empty_stays_empty(self):
        # Several requests default `name` to "" and mean it.
        assert _label("") == ""

    def test_the_rule_targets_markup_not_weirdness(self):
        # A ShellShock string is not an XSS weakness and not ours to refuse;
        # narrowing the rule to markup is the point.
        assert _label("() { :;}; /bin/sleep 15") == "() { :;}; /bin/sleep 15"


class TestSpecRule:
    def test_a_real_plot_spec_passes(self):
        spec = {"chart": "bar", "x": "year", "y": "value",
                "series": [{"y": "total", "label": "Total"}],
                "corrCols": ["a", "b"], "bivariate": False, "level": 2}
        assert _spec(spec) == spec

    def test_markup_nested_in_the_spec_is_rejected(self):
        with pytest.raises(ValueError, match="series"):
            _spec({"chart": "bar", "series": [{"y": "<script>alert(1);</script>"}]})

    def test_markup_in_a_key_the_studio_never_writes_is_still_rejected(self):
        # The check does not know the vocabulary, so a new key is covered too.
        with pytest.raises(ValueError, match="HTML or XML tags"):
            _spec({"someFutureKey": "<img src=x>"})

    def test_none_passes_through(self):
        assert _spec(None) is None


class TestLabelsOverHTTP:
    async def _s(self, services, *names):
        for n in names:
            await seed_user(services["user_repo"], n)

    def test_a_scanner_cannot_store_markup_as_a_dossier_name(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._s(services, "user-1"))
        h = make_headers("user-1")
        bad = client.post("/dossiers", json={"name": "<script>alert(1);</script>"}, headers=h)
        assert bad.status_code == 422
        assert client.get("/dossiers", headers=h).json() == []

    def test_a_real_name_still_works(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._s(services, "user-1"))
        h = make_headers("user-1")
        ok = client.post("/dossiers", json={"name": "Contratos < 2020"}, headers=h)
        assert ok.status_code == 201
        assert ok.json()["name"] == "Contratos < 2020"

    def test_rename_is_guarded_too(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._s(services, "user-1"))
        h = make_headers("user-1")
        did = client.post("/dossiers", json={"name": "Files"}, headers=h).json()["id"]
        bad = client.put(f"/dossiers/{did}", json={"name": "</b><script>x</script>"}, headers=h)
        assert bad.status_code == 422
        assert client.get(f"/dossiers/{did}", headers=h).json()["name"] == "Files"

    def test_a_plot_spec_cannot_carry_markup(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._s(services, "user-1"))
        h = make_headers("user-1")
        pid = client.post("/studio/projects", json={"name": "P"}, headers=h).json()["id"]
        bad = client.post(f"/studio/projects/{pid}/plots",
                          json={"name": "Plot", "spec": {"chart": "<script>alert(1);</script>"}},
                          headers=h)
        assert bad.status_code == 422

    def test_a_real_plot_spec_is_accepted(self, client, services):
        asyncio.get_event_loop().run_until_complete(self._s(services, "user-1"))
        h = make_headers("user-1")
        pid = client.post("/studio/projects", json={"name": "P"}, headers=h).json()["id"]
        ok = client.post(f"/studio/projects/{pid}/plots",
                         json={"name": "Plot", "spec": {"chart": "bar", "x": "year"}},
                         headers=h)
        assert ok.status_code == 201
