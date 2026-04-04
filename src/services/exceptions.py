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
    def __init__(self, message: str = "Conflict") -> None:
        self.message = message
        super().__init__(message)
