"""An id that matches nothing must not come back as an empty entity.

Reported from production: the assistant answered

    The entity with ID "E8D4C4E5-3F7D-4F3E-B2C1-1234567890AB" is a Company
    (unknown country) with no EU procurement contracts in the graph.

about an id that was never in the graph. None of that sentence came from
the model — "unknown country" and "with no EU procurement contracts in the
graph" are strings we generate.

fontem-api answers /companies/<anything> with 200 and a skeleton: the id
echoed back, every other field null. _investigate read status < 400 as
"found", and _build_summary turned the skeleton into a fluent negative
finding. A fabricated fact, manufactured by us, handed to the model as
evidence — on a platform whose entire claim is that figures trace back to
a source.

The second bug is in the same sentence: _build_summary read props["name"],
but /companies returns `company_name`. Every summary said "(unnamed)",
even for entities that plainly had a name, which is why the model
substituted the raw id.
"""
# pylint: disable=protected-access
import asyncio
import json

from src.assistant.mistral_client import (
    MistralProxyClient,
    _build_summary,
    entity_name,
)

# What fontem-api actually returns for an id that matches nothing.
SKELETON = {
    "gmr_id": "E8D4C4E5-3F7D-4F3E-B2C1-1234567890AB",
    "company_name": None,
    "country": None,
    "contract_count": 0,
    "total_contract_value_eur": 0,
    "recent_contracts": [],
    "directors": [],
}
# What it returns for a real one.
REAL = {
    "gmr_id": "867f66f4-4aa4-5737-9bed-d51e2746a729",
    "company_name": "Siemens Energy AG/ADR",
    "country": "DE",
    "contract_count": 0,
}


def test_a_skeleton_has_no_name_and_is_therefore_not_an_entity():
    assert entity_name(SKELETON) == ""


def test_a_real_profile_yields_its_name():
    # /companies returns company_name, not name. Reading only "name" is
    # what made every summary "(unnamed)".
    assert entity_name(REAL) == "Siemens Energy AG/ADR"


def test_authority_and_search_shapes_also_resolve():
    assert entity_name({"authority_name": "DG HOME"}) == "DG HOME"
    assert entity_name({"name": "Frontex"}) == "Frontex"


def test_the_summary_uses_the_real_name():
    out = _build_summary("Company", REAL, 0)
    assert "Siemens Energy AG/ADR" in out
    assert "(unnamed)" not in out


def test_the_summary_reports_country_when_present():
    assert "DE" in _build_summary("Company", REAL, 0)


def test_the_exact_reported_sentence_is_no_longer_producible_from_a_skeleton():
    # Guard on the literal failure. If a skeleton ever reaches
    # _build_summary again this catches it, even if _investigate's check
    # is refactored away.
    out = _build_summary("Company", SKELETON, 0)
    assert out == "(unnamed) is a Company (unknown country) with no EU " \
                  "procurement contracts in the graph."
    # ...which is precisely why a skeleton must never get this far. The
    # caller's job is to return not-found instead; see _investigate.


def test_contract_counts_still_render():
    assert "3 EU procurement contract(s)" in _build_summary("Company", REAL, 3)


class _Resp:
    """Minimal httpx-alike."""

    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


class _FakeClient:
    """Answers every GET the way fontem-api does: 200 with a skeleton."""

    def __init__(self, profile):
        self._profile = profile
        self.calls = []

    async def get(self, url, params=None, **_):  # noqa: ARG002 - httpx signature
        del params
        self.calls.append(url)
        if "/contracts" in url:
            return _Resp(200, [])
        if "/graph/" in url:
            return _Resp(200, {})
        return _Resp(200, self._profile)


async def _investigate_with(profile):
    client = MistralProxyClient(api_key="k", gmr_api_url="http://fake")
    return json.loads(await client._investigate(_FakeClient(profile), "some-id"))


def _run(coro):
    """Each test gets its own loop; there is no session-wide one here."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_investigate_reports_not_found_for_a_skeleton():
    out = _run(_investigate_with(SKELETON))
    assert "error" in out, "a 200 skeleton must not be reported as an entity"
    assert "not found" in out["error"]
    assert "summary" not in out


def test_investigate_returns_a_packet_for_a_real_entity():
    out = _run(_investigate_with(REAL))
    assert "error" not in out
    assert "Siemens Energy AG/ADR" in out["summary"]
