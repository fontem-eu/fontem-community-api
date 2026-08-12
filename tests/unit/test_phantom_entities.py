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


# ── Label dispatch ─────────────────────────────────────────────
#
# The skeleton has a second consequence, found by ASSIST-22 in the staging
# gate. Dispatch used to be `GET /companies/<id>; if 404: try Authority`.
# /companies never 404s — it returns the 200 skeleton — so the Authority
# leg was unreachable and every authority was diagnosed as a nonexistent
# company. Metro Mondego (1 contract, €986,546.64) came back to the user
# as "The entity ID provided does not correspond to any known entity".
#
# Both failure modes trace to the same fact, and one rule settles both:
# a real entity has a name, a skeleton does not, so the name picks the
# label. These tests pin that rule from both sides.

# The real /authorities/<id> body for Metro Mondego, verbatim from
# fontem-testing. Note `authority_name` — not `name`, not `company_name`.
METRO_MONDEGO = {
    "authority_id": "0dafc96e-c142-58eb-9af2-61ed40be0037",
    "authority_name": "Metro Mondego, S. A.",
    "country": "PRT",
    "contract_count": 1,
    "total_spend_eur": 986546.64,
    "recent_contracts": [],
}
# The skeleton /companies/<id> hands back for that same authority id.
AUTHORITY_AS_COMPANY_SKELETON = {
    "gmr_id": "0dafc96e-c142-58eb-9af2-61ed40be0037",
    "company_name": None,
    "country": None,
    "contract_count": 0,
    "total_contract_value_eur": 0,
}


class _ByPathClient:
    """Answers per endpoint, the way fontem-api actually does.

    The older _FakeClient returns one profile for every URL, which cannot
    express this bug at all: it makes /companies and /authorities agree.
    """

    def __init__(self, *, company=None, authority=None, company_status=200):
        self._company = company
        self._authority = authority
        self._company_status = company_status
        self.calls = []

    async def get(self, url, params=None, **_):  # noqa: ARG002 - httpx signature
        del params
        self.calls.append(url)
        if "/contracts" in url:
            return _Resp(200, [])
        if "/graph/" in url:
            return _Resp(200, {})
        if "/companies/" in url:
            return _Resp(self._company_status, self._company)
        return _Resp(200, self._authority)


def _investigate_paths(**kw):
    client = MistralProxyClient(api_key="k", gmr_api_url="http://fake")
    fake = _ByPathClient(**kw)
    out = json.loads(_run(client._investigate(fake, "some-id")))
    return out, fake


def test_an_authority_is_found_even_though_companies_answers_200():
    # The exact reported failure.
    out, _ = _investigate_paths(
        company=AUTHORITY_AS_COMPANY_SKELETON, authority=METRO_MONDEGO,
    )
    assert "error" not in out, (
        "an authority must not be reported as a nonexistent company"
    )
    assert out["label"] == "Authority"
    assert "Metro Mondego, S. A." in out["summary"]


def test_the_authority_leg_is_reached_at_all():
    # Guards the mechanism, not just the outcome: if dispatch ever goes
    # back to keying off 404, /authorities is never requested.
    _, fake = _investigate_paths(
        company=AUTHORITY_AS_COMPANY_SKELETON, authority=METRO_MONDEGO,
    )
    assert any("/authorities/" in u for u in fake.calls)


def test_an_authoritys_contracts_come_from_the_authority_endpoint():
    _, fake = _investigate_paths(
        company=AUTHORITY_AS_COMPANY_SKELETON, authority=METRO_MONDEGO,
    )
    assert any(u.endswith("/authorities/some-id/contracts") for u in fake.calls)
    assert not any("/companies/some-id/contracts" in u for u in fake.calls)


def test_a_company_still_wins_and_short_circuits():
    # Companies are the common case; finding one must not cost an extra
    # request, and must not be relabelled by a stray authority skeleton.
    out, fake = _investigate_paths(company=REAL, authority=METRO_MONDEGO)
    assert out["label"] == "Company"
    assert "Siemens Energy AG/ADR" in out["summary"]
    assert not any("/authorities/some-id" == u.split("http://fake")[-1]
                   for u in fake.calls)


def test_two_skeletons_are_still_not_found():
    out, _ = _investigate_paths(
        company=AUTHORITY_AS_COMPANY_SKELETON,
        authority={"authority_id": "x", "authority_name": None},
    )
    assert "not found" in out["error"]
    assert out["tried_labels"] == ["Company", "Authority"]
    assert "summary" not in out


def test_a_404_on_companies_still_falls_through():
    # The old path handled this one; keep it handled.
    out, _ = _investigate_paths(
        company=None, company_status=404, authority=METRO_MONDEGO,
    )
    assert out["label"] == "Authority"
    assert "Metro Mondego, S. A." in out["summary"]


class _UnparseableResp(_Resp):
    """A 200 whose body is not JSON — e.g. an HTML error page from a proxy."""

    def __init__(self):
        super().__init__(200, None)

    def json(self):
        raise ValueError("not json")


def test_unparseable_json_on_companies_does_not_abort_the_search():
    class _Broken(_ByPathClient):
        async def get(self, url, params=None, **_):
            if url.endswith("/companies/some-id"):
                self.calls.append(url)
                return _UnparseableResp()
            return await super().get(url, params=params)

    client = MistralProxyClient(api_key="k", gmr_api_url="http://fake")
    fake = _Broken(authority=METRO_MONDEGO)
    out = json.loads(_run(client._investigate(fake, "some-id")))
    assert out["label"] == "Authority"


def test_a_non_dict_body_does_not_crash_dispatch():
    out, _ = _investigate_paths(company=["unexpected"], authority=METRO_MONDEGO)
    assert out["label"] == "Authority"


def test_the_reported_user_facing_sentence_cannot_recur_for_metro_mondego():
    # The literal regression: this id, this payload, must not produce a
    # not-found. Pinned on the value so a silent data-shape change trips it.
    out, _ = _investigate_paths(
        company=AUTHORITY_AS_COMPANY_SKELETON, authority=METRO_MONDEGO,
    )
    assert out["props"]["total_spend_eur"] == 986546.64
    assert out["entity_id"] == "some-id"
