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
