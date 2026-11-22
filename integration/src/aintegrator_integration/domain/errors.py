"""Typed errors shared by adapters, service and MCP tools.

Every error carries a stable ``code``, a human-readable ``message`` and a
``retryable`` flag so an LLM-facing tool can suggest a next step without ever
seeing a raw exception traceback.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

__all__ = [
    "AIntegratorError",
    "AmbiguousOutcome",
    "ApprovalDenied",
    "ApprovalRequired",
    "IdempotencyConflict",
    "NotCancellable",
    "NotFound",
    "ProviderProtocolError",
    "ProviderRateLimited",
    "ProviderRejected",
    "ProviderTimeout",
    "ProviderUnavailable",
    "ToolNotAllowed",
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
    """A write was attempted without a granted approval token."""

    code = "APPROVAL_REQUIRED"
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        approval_id: str,
        expires_at: datetime,
        call_arguments: dict[str, Any],
        summary: str = "",
    ) -> None:
        super().__init__(message)
        self.approval_id = approval_id
        self.expires_at = expires_at
        self.call_arguments = call_arguments
        self.summary = summary


class ApprovalDenied(AIntegratorError):
    """An approval was missing, expired, reused or bound to different parameters."""

    code = "APPROVAL_DENIED"
    retryable = False


class ToolNotAllowed(AIntegratorError):
    """The active tool profile does not expose this tool."""

    code = "TOOL_NOT_ALLOWED"
    retryable = False


class IdempotencyConflict(AIntegratorError):
    code = "IDEMPOTENCY_CONFLICT"
    retryable = False
