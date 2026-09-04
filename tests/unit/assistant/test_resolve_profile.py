"""Which label an entity id belongs to, and how that decision is made.

This is the logic ASSIST-22 used to cover from the outside: it asked the
1.7B to investigate "Metro Mondego" and asserted the answer mentioned the
entity at all. That cost 59.6s of a serial promotion gate and tested two
things at once — the model's tool choice and our dispatch — so a failure
never said which had broken. The dispatch is ours; the model's judgement
is not, and the file itself already records that gating on it "tells you
nothing about the platform".

The bug being pinned, from `_resolve_profile`'s own docstring:

    fontem-api answers /companies/<anything> AND /authorities/<anything>
    with a skeleton — the id echoed back and every other field null — so
    an id that was never in the graph comes back looking like a real,
    empty company.

Two consequences, both tested here:

* A `404 -> try Authority` fallthrough cannot work, because /companies
  never 404s. The Authority leg was unreachable and EVERY authority was
  diagnosed as a nonexistent company. Metro Mondego — a real authority
  with 1 contract — came back as "no known entity".
* An unknown id must not resolve to anything. Otherwise the assistant
  reports "X is a Company (unknown country) with no EU procurement
  contracts", which is a confident negative finding about something that
  does not exist — indistinguishable, to a reader, from a real one.
"""
# pylint: disable=protected-access
from __future__ import annotations

import asyncio
import json

from src.assistant.tool_runtime import ToolRuntime

METRO = "0f2a9c11-4d6e-5b8a-9c31-7e2f5a6b8d40"


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _Resp:
    def __init__(self, payload, status=200):
        self.status_code, self._payload = status, payload

    def json(self):
        return self._payload


def _skeleton(id_field: str, entity_id: str) -> dict:
    """What fontem-api returns for an id it has never seen: 200, the id
    echoed back, every other field null. Note there is no name."""
    return {id_field: entity_id, "name": None, "country": None,
            "contract_count": None}


class _Api:
    """A fake that answers PER PATH, which is the whole point — a fake
    that returns one body for every URL cannot tell a company from an
    authority and would pass the bug straight through."""

    def __init__(self, companies=None, authorities=None):
        self._companies = companies or {}
        self._authorities = authorities or {}
        self.calls: list[str] = []

    async def get(self, url, params=None, **_):
        del params
        self.calls.append(url)
        entity_id = url.rstrip("/").rsplit("/", 1)[-1]
        if "/companies/" in url:
            return _Resp(self._companies.get(
                entity_id, _skeleton("gmr_id", entity_id)))
        if "/authorities/" in url:
            return _Resp(self._authorities.get(
                entity_id, _skeleton("authority_id", entity_id)))
        return _Resp({})


def _resolve(api: _Api, entity_id: str = METRO):
    runtime = ToolRuntime(gmr_api_url="http://fake")
    return _run(runtime._resolve_profile(api, entity_id))


class TestDispatchIsOnTheNameNotTheStatusCode:

    def test_a_company_resolves_to_company(self):
        api = _Api(companies={METRO: {"gmr_id": METRO,
                                      "company_name": "Siemens AG",
                                      "country": "DEU"}})
        label, props = _resolve(api)
        assert label == "Company"
        assert props["company_name"] == "Siemens AG"

    def test_an_authority_behind_a_company_skeleton_resolves_to_authority(self):
        """The Metro Mondego regression.

        /companies answers 200 with a skeleton, so status-code dispatch
        stops there and calls it a company. Only looking for a NAME gets
        past it to the Authority leg.
        """
        api = _Api(authorities={METRO: {"authority_id": METRO,
                                        "authority_name": "Metro Mondego",
                                        "country": "PRT"}})
        label, props = _resolve(api)
        assert label == "Authority", (
            "an authority whose /companies answer is a 200 skeleton must "
            "still resolve as an Authority — this is the bug that made "
            "every authority look like a nonexistent company"
        )
        assert props["authority_name"] == "Metro Mondego"

    def test_it_tries_companies_before_authorities(self):
        api = _Api(companies={METRO: {"gmr_id": METRO, "name": "Ambiguous"}},
                   authorities={METRO: {"authority_id": METRO,
                                        "authority_name": "Ambiguous"}})
        label, _ = _resolve(api)
        assert label == "Company"
        assert "/companies/" in api.calls[0]

    def test_an_unknown_id_resolves_to_nothing(self):
        """Skeletons everywhere. The honest answer is "no label"."""
        label, props = _resolve(_Api())
        assert (label, props) == ("", {}), (
            "an id that is in neither index must not resolve — resolving "
            "it manufactures a confident negative finding about an entity "
            "that does not exist"
        )

    def test_it_does_not_stop_at_the_company_skeleton(self):
        """Both legs are actually tried before giving up."""
        api = _Api()
        _resolve(api)
        assert any("/companies/" in c for c in api.calls)
        assert any("/authorities/" in c for c in api.calls), (
            "the Authority leg was never reached — this is exactly what a "
            "404-based fallthrough got wrong, because /companies never 404s"
        )

    def test_an_error_status_falls_through_rather_than_resolving(self):
        class _Erroring(_Api):
            async def get(self, url, params=None, **_):
                del params
                self.calls.append(url)
                if "/companies/" in url:
                    return _Resp({}, status=500)
                return _Resp({"authority_id": METRO,
                              "authority_name": "Metro Mondego"})

        label, _ = _resolve(_Erroring())
        assert label == "Authority"

    def test_a_non_json_body_falls_through_rather_than_raising(self):
        class _Garbage(_Api):
            async def get(self, url, params=None, **_):
                del params
                self.calls.append(url)
                if "/companies/" in url:
                    class _Bad:
                        status_code = 200

                        def json(self):
                            raise ValueError("not json")
                    return _Bad()
                return _Resp({"authority_id": METRO,
                              "authority_name": "Metro Mondego"})

        label, _ = _resolve(_Garbage())
        assert label == "Authority"


class TestWhatTheModelIsToldAboutAnUnknownId:

    def test_it_is_an_error_not_an_empty_entity(self):
        runtime = ToolRuntime(gmr_api_url="http://fake")
        raw = _run(runtime._investigate(_Api(), METRO))
        out = json.loads(raw)
        assert "error" in out, (
            "an unknown id must come back as an error; reporting it as an "
            "entity with no contracts is a fabricated negative finding"
        )
        assert out["tried_labels"] == ["Company", "Authority"]
        assert "not an entity" in out["detail"]
