"""The feed-query contract — the rules that decide if a query can be a feed."""
# pylint: disable=missing-function-docstring
from __future__ import annotations

from src.domain.named_query import NamedQuery
from src.services import feed_contract
from src.services.query_executor import ExecResult
from tests.fake_query_executor import CONTRACT_COLUMNS, ok_result


def _checks_by_id(checks):
    return {c.id: c for c in checks}


def _sql(query: str, **kw) -> NamedQuery:
    return NamedQuery(lang="sql", query=query, **kw)


GOOD_SQL = (
    "SELECT id AS item_id, t AS item_time, geo AS nuts, name AS title, url AS link "
    "FROM contracts WHERE geo = ANY(%(nuts)s) AND t > %(since)s ORDER BY item_time DESC"
)


def test_a_well_formed_sql_query_passes_every_static_check():
    checks = _checks_by_id(feed_contract.static_checks(_sql(GOOD_SQL)))
    assert all(c.passed for c in checks.values()), \
        [(k, v.reason) for k, v in checks.items() if not v.passed]


def test_every_check_carries_a_reason_whether_it_passed_or_failed():
    """Silence is not expressible: a verdict without an explanation is
    exactly what the contract exists to prevent."""
    for query in (_sql(GOOD_SQL), _sql("SELECT 1"), _sql("")):
        for check in feed_contract.static_checks(query):
            assert check.reason.strip(), f"{check.id} passed={check.passed} had no reason"


def test_missing_binds_are_reported_separately_and_explain_why():
    checks = _checks_by_id(feed_contract.static_checks(_sql("SELECT 1 AS item_id")))
    assert not checks["binds_nuts"].passed
    assert not checks["binds_since"].passed
    # The message has to say what to write, not just that something is absent.
    assert "%(nuts)s" in checks["binds_nuts"].reason
    assert "rescans" in checks["binds_since"].reason


def test_bind_detection_uses_each_engines_own_syntax():
    # SQL placeholders in a Cypher query are not Cypher binds.
    cypher = NamedQuery(lang="cypher", query="MATCH (c) WHERE c.geo IN %(nuts)s RETURN c")
    assert not _checks_by_id(feed_contract.static_checks(cypher))["binds_nuts"].passed

    cypher_ok = NamedQuery(
        lang="cypher",
        query="MATCH (c) WHERE c.geo IN $nuts AND c.t > $since RETURN c",
    )
    checks = _checks_by_id(feed_contract.static_checks(cypher_ok))
    assert checks["binds_nuts"].passed and checks["binds_since"].passed


def test_sparql_is_storable_but_cannot_satisfy_the_binds():
    query = NamedQuery(lang="sparql", query="SELECT ?s WHERE { ?s ?p ?o }")
    checks = _checks_by_id(feed_contract.static_checks(query))
    assert checks["lang"].passed          # accepted by the catalogue
    assert not checks["engine_supports_binds"].passed
    assert "no bind-parameter protocol" in checks["engine_supports_binds"].reason


def test_write_keywords_are_caught_before_execution():
    checks = _checks_by_id(feed_contract.static_checks(_sql("DELETE FROM contracts")))
    assert not checks["read_only"].passed
    assert "DELETE" in checks["read_only"].reason


def test_missing_required_columns_are_named():
    result = ok_result(columns=["item_id", "title"], rows=[["a", "b"]])
    checks = _checks_by_id(feed_contract.runtime_checks(result, result))
    assert not checks["columns"].passed
    for missing in ("item_time", "nuts", "link"):
        assert missing in checks["columns"].reason


def test_duplicate_item_ids_fail_because_readers_would_collapse_them():
    rows = [
        ["same", "2026-08-01T00:00:00+00:00", "PT", "A", "https://x/1"],
        ["same", "2026-08-02T00:00:00+00:00", "PT", "B", "https://x/2"],
    ]
    checks = _checks_by_id(feed_contract.runtime_checks(ok_result(rows), ok_result(rows)))
    assert not checks["item_id_unique"].passed


def test_a_positional_item_id_is_caught_by_the_second_run():
    """A row_number() masquerading as an id looks perfect on one run and
    re-notifies every subscriber the moment a row is inserted above it."""
    first = ok_result([["1", "2026-08-01T00:00:00+00:00", "PT", "A", "https://x/1"]])
    second = ok_result([["2", "2026-08-01T00:00:00+00:00", "PT", "A", "https://x/1"]])
    checks = _checks_by_id(feed_contract.runtime_checks(first, second))
    assert checks["item_id_unique"].passed        # a single run says nothing
    assert not checks["item_id_stable"].passed
    assert "row position" in checks["item_id_stable"].reason


def test_an_unparseable_item_time_fails():
    rows = [["a", "not-a-date", "PT", "A", "https://x/1"]]
    checks = _checks_by_id(feed_contract.runtime_checks(ok_result(rows), ok_result(rows)))
    assert not checks["item_time_parses"].passed


def test_an_empty_result_is_not_a_failure_but_says_so():
    """A quiet window is a normal state for a feed. Failing here would make
    every low-volume query un-publishable."""
    empty = ExecResult(columns=list(CONTRACT_COLUMNS), rows=[], row_count=0)
    checks = _checks_by_id(feed_contract.runtime_checks(empty, empty))
    assert checks["rows"].passed
    assert "nothing was checked" in checks["rows"].reason


def test_an_execution_error_short_circuits_with_the_engines_message():
    failed = ExecResult(error="SQL error: relation \"nope\" does not exist")
    checks = _checks_by_id(feed_contract.runtime_checks(failed, None))
    assert not checks["executes"].passed
    assert "nope" in checks["executes"].reason
    assert "columns" not in checks     # nothing else is worth reporting


def test_a_waiver_needs_a_written_reason():
    checks = feed_contract.static_checks(_sql("SELECT 1 AS item_id"))
    # Empty reason: not a waiver.
    waived = _checks_by_id(feed_contract.apply_waivers(checks, {"binds_nuts": "   "}))
    assert not waived["binds_nuts"].waived

    waived = _checks_by_id(feed_contract.apply_waivers(
        checks, {"binds_nuts": "legal acts are EU-level and have no region"}))
    assert waived["binds_nuts"].waived
    assert "EU-level" in waived["binds_nuts"].reason


def test_only_the_bind_checks_can_be_waived():
    """A feed without a stable id is not a feed, it is a re-notification bug.
    No amount of explanation makes it subscribable."""
    rows = [["same", "2026-08-01T00:00:00+00:00", "PT", "A", "https://x/1"],
            ["same", "2026-08-02T00:00:00+00:00", "PT", "B", "https://x/2"]]
    checks = feed_contract.runtime_checks(ok_result(rows), ok_result(rows))
    waived = _checks_by_id(feed_contract.apply_waivers(
        checks, {"item_id_unique": "we are fine with duplicates honestly"}))
    assert not waived["item_id_unique"].waived
    assert not feed_contract.is_subscribable(list(waived.values()))


def test_is_subscribable_accepts_waived_failures_only():
    checks = feed_contract.static_checks(_sql("SELECT 1 AS item_id"))
    assert not feed_contract.is_subscribable(checks)
    waived = feed_contract.apply_waivers(checks, {
        "binds_nuts": "EU-level",
        "binds_since": "snapshot, republished whole each run",
    })
    assert feed_contract.is_subscribable(waived)


def test_sample_params_supply_both_standard_binds():
    params = feed_contract.sample_params()
    assert set(params) == {"nuts", "since"}
    assert isinstance(params["nuts"], list)


def test_declared_defaults_are_supplied_to_validation():
    """A query needing a third bind used to fail validation with the engine's
    "Expected parameter(s)", which reads like a broken query rather than a
    catalogue that never asked what the parameter should be."""
    from src.domain.named_query import QueryParam  # pylint: disable=import-outside-toplevel
    declared = [
        QueryParam(name="percentile", type="number", default=0.95),
        QueryParam(name="reference_since", type="timestamp", default="2025-08-14T00:00:00+00:00"),
    ]
    params = feed_contract.sample_params(declared)
    assert params["percentile"] == 0.95
    assert params["reference_since"] == "2025-08-14T00:00:00+00:00"
    assert set(params) >= {"nuts", "since"}


def test_a_declared_parameter_without_a_default_is_left_absent():
    """The resulting failure names the parameter, which is the useful message."""
    from src.domain.named_query import QueryParam  # pylint: disable=import-outside-toplevel
    params = feed_contract.sample_params([QueryParam(name="cohort", type="text")])
    assert "cohort" not in params


def test_a_query_cannot_redefine_the_standard_binds():
    from src.domain.named_query import QueryParam  # pylint: disable=import-outside-toplevel
    declared = [QueryParam(name="nuts", type="text[]", default=["XX"]),
                QueryParam(name="since", type="timestamp", default="1999-01-01")]
    params = feed_contract.sample_params(declared)
    assert params["nuts"] == list(feed_contract.SAMPLE_NUTS)
    assert params["since"] != "1999-01-01"
