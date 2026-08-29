"""The OpenAPI spec IS the provider contract — pin its load-bearing parts.

The contracts CI job cross-validates consumer pacts against the spec
generated from code; these tests pin the spec-side guarantees so a
regression fails here first, with a named reason, rather than as an
opaque pact-validation error.
"""
import io
import json
from contextlib import redirect_stdout

from src.api.app import build_app

from scripts.generate_openapi import main as generate_openapi_main


def _spec():
    return build_app(database_url="postgresql://spec:spec@localhost/spec").openapi()


def test_generate_openapi_emits_valid_spec_offline():
    buf = io.StringIO()
    with redirect_stdout(buf):
        generate_openapi_main()
    spec = json.loads(buf.getvalue())
    assert spec["openapi"].startswith("3.")
    assert len(spec["paths"]) > 100


def test_every_operation_declares_the_lang_query_param():
    spec = _spec()
    missing = []
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            if method not in ("get", "post", "put", "delete", "patch"):
                continue
            params = op.get("parameters", [])
            has_lang = any(
                p.get("name") == "lang" and p.get("in") in ("query", "path")
                for p in params
            )
            if not has_lang:
                missing.append(f"{method.upper()} {path}")
    assert not missing, f"operations without a lang declaration: {missing[:5]}"


def test_report_response_contract_fields():
    spec = _spec()
    schema = spec["components"]["schemas"]["ReportResponse"]
    declared = set(schema["properties"])
    # The fields consumers (fontem-web pacts) rely on.
    assert {"id", "title", "abstract", "visibility", "language"} <= declared
    assert set(schema.get("required", [])) >= {"id", "title"}
    # Enrichments must stay allowed — handlers add sections/content_doc/tags.
    assert schema.get("additionalProperties", True) is not False


def test_search_items_carry_tags():
    spec = _spec()
    schema = spec["components"]["schemas"]["ReportSearchItem"]
    assert "tags" in schema["properties"]


def test_followed_tags_response_shape():
    spec = _spec()
    schema = spec["components"]["schemas"]["FollowedTagsResponse"]
    assert schema["properties"]["tags"]["type"] == "array"
    assert "tags" in schema.get("required", [])
