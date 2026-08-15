"""Admin API for the feed-query catalogue."""
# pylint: disable=missing-function-docstring,redefined-outer-name,unused-argument
# ── the `admin` / `contributor` fixtures seed a user as a side effect; the
#    test body never needs the returned object. Same for `services` where the
#    assertion is on the HTTP response rather than the fake.
from __future__ import annotations

import asyncio

import pytest

from src.services.query_executor import ExecResult
from tests.conftest import make_headers, seed_user
from tests.fake_query_executor import CONTRACT_COLUMNS, ok_result

GOOD_SQL = (
    "SELECT id AS item_id, t AS item_time, geo AS nuts, name AS title, url AS link "
    "FROM contracts WHERE geo = ANY(%(nuts)s) AND t > %(since)s ORDER BY item_time DESC"
)

ADMIN = "admin-1"
CONTRIB = "contrib-1"


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture()
def admin(services):
    return _run(seed_user(services["user_repo"], ADMIN, trust_level="admin"))


@pytest.fixture()
def contributor(services):
    return _run(seed_user(services["user_repo"], CONTRIB, trust_level="contributor"))


def _create(client, **overrides):
    body = {"slug": "public-contracts", "name": "Public contracts",
            "lang": "sql", "query": GOOD_SQL}
    body.update(overrides)
    return client.post("/admin/named-queries", json=body, headers=make_headers(ADMIN))


# ── authorisation ───────────────────────────────────────────────
def test_a_contributor_cannot_touch_the_catalogue(client, contributor):
    """Data Studio is contributor-level; the catalogue is not. A named query
    is published to every visitor and then run on a schedule."""
    assert client.get("/admin/named-queries",
                      headers=make_headers(CONTRIB)).status_code == 403
    assert client.post("/admin/named-queries", json={"slug": "x", "query": "SELECT 1"},
                       headers=make_headers(CONTRIB)).status_code == 403
    assert client.get("/admin/query-groups",
                      headers=make_headers(CONTRIB)).status_code == 403


def test_the_catalogue_requires_authentication(client):
    assert client.get("/admin/named-queries").status_code in (401, 403)


def test_the_public_picker_is_anonymous(client):
    resp = client.get("/query-groups")
    assert resp.status_code == 200
    assert resp.json() == []


# ── named queries ───────────────────────────────────────────────
def test_create_and_read_back(client, admin):
    resp = _create(client)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["slug"] == "public-contracts"
    assert body["status"] == "draft"
    assert body["contract_ok"] is False

    got = client.get(f"/admin/named-queries/{body['id']}", headers=make_headers(ADMIN))
    assert got.status_code == 200
    assert got.json()["groups"] == []


def test_slugs_are_unique_and_shaped(client, admin):
    assert _create(client).status_code == 201
    assert _create(client).status_code == 409
    assert _create(client, slug="Not A Slug").status_code == 400
    assert _create(client, slug="trailing-").status_code == 400


def test_validation_records_a_verdict_with_a_cost(client, admin, services):
    services["query_executor"].push(ok_result(duration_ms=42), ok_result(duration_ms=39))
    query_id = _create(client).json()["id"]

    resp = client.post(f"/admin/named-queries/{query_id}/validate",
                       headers=make_headers(ADMIN))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["contract_ok"] is True
    report = body["contract_report"]
    assert report["subscribable"] is True
    assert report["duration_ms"] == 42          # the cost signal is recorded
    assert report["columns"] == CONTRACT_COLUMNS
    assert body["validated_at"] is not None
    assert all(c["reason"] for c in report["checks"])


def test_validation_runs_the_query_twice_with_identical_binds(client, admin, services):
    """The second run is what catches a positional item_id."""
    executor = services["query_executor"]
    query_id = _create(client).json()["id"]
    client.post(f"/admin/named-queries/{query_id}/validate", headers=make_headers(ADMIN))

    runs = [c for c in executor.calls if c["query"] == GOOD_SQL]
    assert len(runs) == 2
    assert runs[0]["params"] == runs[1]["params"]
    assert set(runs[0]["params"]) == {"nuts", "since"}


def test_a_failing_query_is_not_subscribable_and_says_why(client, admin, services):
    services["query_executor"].push(ExecResult(error="SQL error: no such table"))
    query_id = _create(client).json()["id"]
    body = client.post(f"/admin/named-queries/{query_id}/validate",
                       headers=make_headers(ADMIN)).json()
    assert body["contract_ok"] is False
    failed = [c for c in body["contract_report"]["checks"] if not c["passed"]]
    assert failed and "no such table" in failed[0]["reason"]


def test_a_static_failure_skips_execution_entirely(client, admin, services):
    executor = services["query_executor"]
    query_id = _create(client, slug="bad", query="DELETE FROM contracts").json()["id"]
    body = client.post(f"/admin/named-queries/{query_id}/validate",
                       headers=make_headers(ADMIN)).json()
    assert body["contract_ok"] is False
    assert not [c for c in executor.calls if "DELETE" in c["query"]]


# ── publication gating ──────────────────────────────────────────
def test_publishing_is_gated_on_the_contract(client, admin, services):
    query_id = _create(client).json()["id"]
    resp = client.patch(f"/admin/named-queries/{query_id}",
                        json={"status": "published"}, headers=make_headers(ADMIN))
    assert resp.status_code == 400
    assert "contract" in resp.json()["detail"].lower()

    client.post(f"/admin/named-queries/{query_id}/validate", headers=make_headers(ADMIN))
    resp = client.patch(f"/admin/named-queries/{query_id}",
                        json={"status": "published"}, headers=make_headers(ADMIN))
    assert resp.status_code == 200
    assert resp.json()["status"] == "published"


def test_editing_the_body_invalidates_the_verdict_and_unpublishes(client, admin, services):
    """A stored 'yes' that refers to a previous version of the query is worse
    than no verdict at all."""
    query_id = _create(client).json()["id"]
    client.post(f"/admin/named-queries/{query_id}/validate", headers=make_headers(ADMIN))
    client.patch(f"/admin/named-queries/{query_id}", json={"status": "published"},
                 headers=make_headers(ADMIN))

    body = client.patch(f"/admin/named-queries/{query_id}",
                        json={"query": GOOD_SQL + " LIMIT 10"},
                        headers=make_headers(ADMIN)).json()
    assert body["contract_ok"] is False
    assert body["contract_report"] is None
    assert body["status"] == "draft"


def test_renaming_does_not_invalidate_the_verdict(client, admin, services):
    query_id = _create(client).json()["id"]
    client.post(f"/admin/named-queries/{query_id}/validate", headers=make_headers(ADMIN))
    client.patch(f"/admin/named-queries/{query_id}", json={"status": "published"},
                 headers=make_headers(ADMIN))

    body = client.patch(f"/admin/named-queries/{query_id}",
                        json={"name": "Public contracts, by region",
                              "description": "Clearer wording"},
                        headers=make_headers(ADMIN)).json()
    assert body["contract_ok"] is True
    assert body["status"] == "published"


# ── preview ─────────────────────────────────────────────────────
def test_preview_runs_an_unsaved_draft(client, admin, services):
    resp = client.post("/admin/named-queries/preview",
                       json={"lang": "sql", "query": GOOD_SQL},
                       headers=make_headers(ADMIN))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["columns"] == CONTRACT_COLUMNS
    assert body["rows"]
    assert body["contract"]["subscribable"] is True
    # The sample binds are reported so the author knows what was substituted.
    assert set(body["params_used"]) >= {"nuts", "since"}


def test_preview_caps_the_rows_it_hands_back(client, admin, services):
    rows = [[f"c{i}", "2026-08-01T00:00:00+00:00", "PT", "t", "https://x"]
            for i in range(200)]
    services["query_executor"].default = ok_result(rows)
    body = client.post("/admin/named-queries/preview",
                       json={"lang": "sql", "query": GOOD_SQL},
                       headers=make_headers(ADMIN)).json()
    assert len(body["rows"]) == 50
    assert body["truncated"] is True
    assert body["row_count"] == 200


# ── groups ──────────────────────────────────────────────────────
def _group(client, slug="public-investment", **kw):
    body = {"slug": slug, "name": slug.replace("-", " ").title()}
    body.update(kw)
    return client.post("/admin/query-groups", json=body, headers=make_headers(ADMIN))


def test_a_query_can_belong_to_several_groups(client, admin):
    query_id = _create(client).json()["id"]
    first = _group(client, "public-investment").json()["id"]
    second = _group(client, "energy").json()["id"]

    for group_id in (first, second):
        resp = client.put(f"/admin/query-groups/{group_id}/queries",
                          json={"query_ids": [query_id]}, headers=make_headers(ADMIN))
        assert resp.status_code == 200, resp.text
        assert [q["id"] for q in resp.json()["queries"]] == [query_id]

    got = client.get(f"/admin/named-queries/{query_id}", headers=make_headers(ADMIN)).json()
    assert sorted(g["slug"] for g in got["groups"]) == ["energy", "public-investment"]


def test_membership_is_ordered_and_replaced_wholesale(client, admin):
    first = _create(client, slug="q-one").json()["id"]
    second = _create(client, slug="q-two").json()["id"]
    group_id = _group(client).json()["id"]

    resp = client.put(f"/admin/query-groups/{group_id}/queries",
                      json={"query_ids": [second, first]}, headers=make_headers(ADMIN))
    assert [q["id"] for q in resp.json()["queries"]] == [second, first]

    resp = client.put(f"/admin/query-groups/{group_id}/queries",
                      json={"query_ids": [first]}, headers=make_headers(ADMIN))
    assert [q["id"] for q in resp.json()["queries"]] == [first]


def test_membership_rejects_an_unknown_query(client, admin):
    group_id = _group(client).json()["id"]
    resp = client.put(f"/admin/query-groups/{group_id}/queries",
                      json={"query_ids": ["11111111-1111-1111-1111-111111111111"]},
                      headers=make_headers(ADMIN))
    assert resp.status_code == 404


def test_duplicates_in_a_membership_payload_are_collapsed(client, admin):
    query_id = _create(client).json()["id"]
    group_id = _group(client).json()["id"]
    resp = client.put(f"/admin/query-groups/{group_id}/queries",
                      json={"query_ids": [query_id, query_id]},
                      headers=make_headers(ADMIN))
    assert [q["id"] for q in resp.json()["queries"]] == [query_id]


def test_deleting_a_query_removes_it_from_its_groups(client, admin):
    query_id = _create(client).json()["id"]
    group_id = _group(client).json()["id"]
    client.put(f"/admin/query-groups/{group_id}/queries",
               json={"query_ids": [query_id]}, headers=make_headers(ADMIN))

    assert client.delete(f"/admin/named-queries/{query_id}",
                         headers=make_headers(ADMIN)).status_code == 204
    got = client.get(f"/admin/query-groups/{group_id}", headers=make_headers(ADMIN))
    assert got.json()["queries"] == []


# ── the public catalogue ────────────────────────────────────────
def test_the_public_picker_shows_only_published_queries(client, admin, services):
    draft = _create(client, slug="draft-query").json()["id"]
    published = _create(client, slug="published-query").json()["id"]
    client.post(f"/admin/named-queries/{published}/validate", headers=make_headers(ADMIN))
    client.patch(f"/admin/named-queries/{published}", json={"status": "published"},
                 headers=make_headers(ADMIN))

    group_id = _group(client).json()["id"]
    client.put(f"/admin/query-groups/{group_id}/queries",
               json={"query_ids": [draft, published]}, headers=make_headers(ADMIN))

    body = client.get("/query-groups").json()
    assert len(body) == 1
    assert [q["slug"] for q in body[0]["queries"]] == ["published-query"]


def test_an_admin_only_group_stays_out_of_the_public_picker(client, admin, services):
    query_id = _create(client).json()["id"]
    client.post(f"/admin/named-queries/{query_id}/validate", headers=make_headers(ADMIN))
    client.patch(f"/admin/named-queries/{query_id}", json={"status": "published"},
                 headers=make_headers(ADMIN))
    group_id = _group(client, "staging", visibility="admin").json()["id"]
    client.put(f"/admin/query-groups/{group_id}/queries",
               json={"query_ids": [query_id]}, headers=make_headers(ADMIN))

    assert client.get("/query-groups").json() == []


def test_a_group_with_nothing_published_is_omitted_not_shown_empty(client, admin):
    query_id = _create(client).json()["id"]
    group_id = _group(client).json()["id"]
    client.put(f"/admin/query-groups/{group_id}/queries",
               json={"query_ids": [query_id]}, headers=make_headers(ADMIN))
    assert client.get("/query-groups").json() == []


def test_a_query_with_extra_binds_can_be_validated(client, admin, services):
    """Relevance-scoped queries need a third and fourth bind. Before declared
    defaults were passed through, validating one failed on the engine's
    "Expected parameter(s)" and it could never be published."""
    executor = services["query_executor"]
    body = {
        "slug": "scoped", "name": "Scoped", "lang": "cypher",
        "query": ("MATCH (c) WHERE c.geo IN $nuts AND c.t > $since "
                  "AND c.v >= $threshold AND c.t > $reference_since RETURN c"),
        "params": [
            {"name": "percentile", "type": "number", "default": 0.95},
            {"name": "reference_since", "type": "timestamp", "default": "2025-08-14T00:00:00+00:00"},
        ],
    }
    query_id = client.post("/admin/named-queries", json=body,
                           headers=make_headers(ADMIN)).json()["id"]
    client.post(f"/admin/named-queries/{query_id}/validate", headers=make_headers(ADMIN))

    sent = executor.calls[-1]["params"]
    assert sent["percentile"] == 0.95
    assert sent["reference_since"] == "2025-08-14T00:00:00+00:00"
    assert set(sent) >= {"nuts", "since"}
