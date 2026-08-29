"""Emit the API's OpenAPI spec from code — no server, no database.

The contract pipeline's provider side: the spec IS the provider
contract, generated at any commit and cross-validated statically
against the consumer pacts (see the `contracts` CI job).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.api.app import build_app  # noqa: E402  pylint: disable=wrong-import-position


def main() -> None:
    # The URL is never connected for spec generation; build_app only
    # needs one so it wires the container.
    app = build_app(database_url="postgresql://spec:spec@localhost/spec")
    json.dump(app.openapi(), sys.stdout, indent=1, sort_keys=True)


if __name__ == "__main__":
    main()
