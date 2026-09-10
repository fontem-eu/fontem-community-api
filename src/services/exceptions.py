from __future__ import annotations


class PermissionDenied(Exception):
    def __init__(self, message: str = "Permission denied") -> None:
        self.message = message
        super().__init__(message)


class NotFound(Exception):
    def __init__(self, message: str = "Not found") -> None:
        self.message = message
        super().__init__(message)


class Conflict(Exception):
    """A write that cannot be applied to the current state.

    ``payload`` rides along to the 409 body. A conflict the caller can
    only be told about is a dead end; one that arrives with the current
    state attached can be shown as a difference and resolved in place.
    """

    def __init__(self, message: str = "Conflict",
                 payload: dict | None = None) -> None:
        self.message = message
        self.payload = payload or {}
        super().__init__(message)


class InvalidInput(Exception):
    """Caller-provided data failed validation (slug, limit, etc.).
    Maps to HTTP 400 in the router layer."""
    def __init__(self, message: str = "Invalid input") -> None:
        self.message = message
        super().__init__(message)


class StoreUnavailable(Exception):
    """A dependency the operation needed could not be reached.

    Distinct from InvalidInput on purpose. The caller did nothing wrong
    and there is nothing for them to fix; the right answer is 503 and a
    retry, not 400 and an edit.

    It exists because the alternative is worse than an unhelpful error
    message: validate_query DEMOTES a published query that fails to
    validate, and that demotion is persisted. Treating an outage as a
    failed validation un-published three briefings on a single Neo4j
    OOMKill and left the public landing feed empty after the store
    recovered.
    """

    def __init__(self, message: str = "Store unavailable") -> None:
        self.message = message
        super().__init__(message)
