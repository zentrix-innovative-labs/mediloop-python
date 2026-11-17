"""What this library raises."""
from __future__ import annotations

from typing import Any


class MediloopError(Exception):
    """Anything this library raises."""


class ConnectionFailedError(MediloopError):
    """No answer came back at all (network failure, timeout)."""


class ApiError(MediloopError):
    """Mediloop answered with a refusal. ``error_code`` is Mediloop's reason code, for example ``route_not_open``."""

    def __init__(self, status: int, error_code: str | None, message: str, body: Any = None, retry_after: str | None = None):
        super().__init__(message)
        self.status = status
        self.error_code = error_code
        self.message = message
        self.body = body
        self.retry_after = retry_after


class AuthenticationError(ApiError):
    pass


class PermissionDeniedError(ApiError):
    pass


class NotFoundError(ApiError):
    pass


class ConflictError(ApiError):
    pass


class RateLimitError(ApiError):
    pass


class ValidationError(ApiError):
    pass


class ServerError(ApiError):
    pass
