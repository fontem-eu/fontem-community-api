"""Shared OpenAPI response metadata for authenticated routers.

Schemathesis flagged ~77 endpoints as returning "undocumented status
codes" because individual handlers only declared the 2xx / 422 shape
in their signatures. Every authenticated endpoint can in practice
return 401 (missing token), 403 (permission / trust-level gate), or
429 (ingress rate limit hit before the handler ran). Attaching these
once at the router level covers them all without touching each
handler.

Usage:

    from src.api.openapi_responses import AUTH_RESPONSES

    router = APIRouter(
        prefix="/reports",
        tags=["reports"],
        responses=AUTH_RESPONSES,
    )
"""
from __future__ import annotations

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
