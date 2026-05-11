"""Shared OpenAPI response metadata for routers.

The DAST runs (schemathesis) flagged endpoints as returning
"undocumented status codes" because individual handlers only declared
the 2xx / 422 shape in their signatures. Two flavours of shared
responses live here:

  - ``AUTH_RESPONSES`` — every authenticated endpoint can return 401
    (missing token), 403 (permission / trust-level gate), or 429
    (ingress rate limit hit before the handler ran). Attach to
    ``APIRouter(responses=AUTH_RESPONSES)``.

  - ``RESOURCE_RESPONSES`` — extends AUTH_RESPONSES with 400 and 404
    for routers whose path parameters get UUID-bound downstream
    (``{report_id}``, ``{section_id}``, ``{group_id}``, ``{user_id}``,
    ``{access_id}``, ``{flag_id}``, ``{issue_id}``). The DBAPIError
    handler in ``src.api.app`` translates asyncpg's bad-UUID-bind
    ``ValueError`` into a 400 ("Invalid parameter: …") instead of
    leaking a 500. Schemathesis hits these routes with bogus IDs
    during fuzz, gets the legitimate 400, and (without this metadata)
    flagged it as undocumented.

Usage:

    from src.api.openapi_responses import AUTH_RESPONSES, RESOURCE_RESPONSES

    router = APIRouter(
        prefix="/reports",
        tags=["reports"],
        responses=RESOURCE_RESPONSES,
    )
"""
from __future__ import annotations

from typing import Annotated

from fastapi import Path
from pydantic import Field

AUTH_RESPONSES: dict = {
    401: {
        "description": (
            "Authentication required. The caller did not supply a valid "
            "Bearer token, or the token was expired/revoked/banned."
        ),
    },
    403: {
        "description": (
            "Permission denied. Caller is authenticated but the target "
            "resource is owned by someone else, or the caller's trust "
            "level / role is below the threshold for this operation."
        ),
    },
    429: {
        "description": (
            "Rate limited by the ingress. The request never reached the "
            "handler — retry after a short delay."
        ),
    },
}


# Auth + the two outcomes any path-id resource route can hit:
#
#   400 — invalid path parameter shape (asyncpg ValueError → 400 via
#         the DBAPIError handler in src.api.app.build_app). Fires when
#         the caller sends e.g. ``/reports/undefined`` or any non-UUID
#         token in a UUID-bound path slot.
#   404 — resource doesn't exist (or, for hidden private resources,
#         the visibility check returns 404 to avoid leaking existence).
RESOURCE_RESPONSES: dict = {
    **AUTH_RESPONSES,
    400: {
        "description": (
            "Invalid request. Either a path parameter failed to bind "
            "(non-UUID where a UUID was expected) or the request body "
            "violated a value-level constraint not expressible in the "
            "JSON schema (e.g. file too large, password too short)."
        ),
    },
    404: {
        "description": (
            "Resource not found. The path parameter resolved to a row "
            "that doesn't exist, or the row exists but the caller has "
            "no visibility (private reports return 404 rather than 403 "
            "to avoid leaking existence)."
        ),
    },
}


UUID_RE = (
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


# Drop-in replacement for ``: str`` on path parameters whose values get
# bound to a UUID column downstream (``{report_id}``, ``{section_id}``,
# ``{group_id}``, ``{user_id}``, ``{access_id}``, ``{flag_id}``,
# ``{issue_id}``, ``{uid}``). Without the pattern, FastAPI's plain
# ``str`` accepts anything; the asyncpg bind fails at Postgres with a
# 400 from the DBAPIError handler in ``src.api.app``, and fuzz tooling
# (schemathesis) flags every such 400 as "API rejected schema-compliant
# request" because *its* schema view said the input was valid. With
# this Annotated alias FastAPI rejects non-matching path values at
# validation time (422) and emits ``pattern: ^[0-9a-fA-F]{8}-…$`` in
# the OpenAPI schema, so schemathesis generates UUID-shaped strings
# that actually bind cleanly.
#
# Usage:
#
#     from src.api.openapi_responses import UuidPath
#
#     @router.get("/{report_id}")
#     async def get_report(report_id: UuidPath, ...) -> dict:
#         ...
UuidPath = Annotated[str, Path(pattern=UUID_RE)]


# Pydantic-friendly variant for ``UUID``-shaped fields inside request
# bodies (where ``Path()`` doesn't apply). Same regex, emitted as a
# JSON-schema ``pattern`` on the property. Use inside ``BaseModel``
# definitions, e.g. ``target_id: UuidStr`` in CreateFlagRequest.
UuidStr = Annotated[str, Field(pattern=UUID_RE)]
