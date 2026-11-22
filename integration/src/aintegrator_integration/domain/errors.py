"""Typed errors shared by adapters, service and MCP tools.

Every error carries a stable ``code``, a human-readable ``message`` and a
``retryable`` flag so an LLM-facing tool can suggest a next step without ever
seeing a raw exception traceback.
"""

from __future__ import annotations

__all__ = [
    "AIntegratorError",
    "AmbiguousOutcome",
    "ApprovalRequired",
    "IdempotencyConflict",
    "NotCancellable",
    "NotFound",
    "ProviderProtocolError",
    "ProviderRateLimited",
    "ProviderRejected",
    "ProviderTimeout",
    "ProviderUnavailable",
    "UnsupportedOperation",
]


class AIntegratorError(Exception):
    """Base class for all expected domain errors."""

    code = "INTERNAL_ERROR"
    retryable = False

    def __init__(
        self, message: str, *, code: str | None = None, retryable: bool | None = None
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if retryable is not None:
            self.retryable = retryable

    def as_dict(self) -> dict[str, object]:
        return {"code": self.code, "message": self.message, "retryable": self.retryable}


class ProviderUnavailable(AIntegratorError):
    code = "PROVIDER_UNAVAILABLE"
    retryable = True


class ProviderTimeout(AIntegratorError):
    code = "PROVIDER_TIMEOUT"
    retryable = True


class ProviderRateLimited(AIntegratorError):
    code = "PROVIDER_RATE_LIMITED"
    retryable = True

    def __init__(self, message: str, *, retry_after: float = 1.0) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class ProviderRejected(AIntegratorError):
    code = "PROVIDER_REJECTED"
    retryable = False

    def __init__(self, message: str, *, reason: str = "") -> None:
        super().__init__(message)
        self.reason = reason


class ProviderProtocolError(AIntegratorError):
    code = "PROVIDER_PROTOCOL_ERROR"
    retryable = False


class AmbiguousOutcome(AIntegratorError):
    code = "AMBIGUOUS_OUTCOME"
    retryable = False


class UnsupportedOperation(AIntegratorError):
    code = "UNSUPPORTED_OPERATION"
    retryable = False


class NotCancellable(AIntegratorError):
    code = "NOT_CANCELLABLE"
    retryable = False


class NotFound(AIntegratorError):
    code = "NOT_FOUND"
    retryable = False


class ApprovalRequired(AIntegratorError):
    code = "APPROVAL_REQUIRED"
    retryable = False


class IdempotencyConflict(AIntegratorError):
    code = "IDEMPOTENCY_CONFLICT"
    retryable = False
