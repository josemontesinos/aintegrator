"""HMAC verification for skylink webhooks."""

from __future__ import annotations

import hashlib
import hmac
import time

from aintegrator_integration.domain import AIntegratorError

__all__ = ["InvalidSignature", "sign_body", "verify_signature"]


class InvalidSignature(AIntegratorError):
    code = "INVALID_SIGNATURE"
    retryable = False


def sign_body(secret: str, raw_body: bytes, timestamp: int) -> str:
    signature = hmac.new(
        secret.encode(), f"{timestamp}.".encode() + raw_body, hashlib.sha256
    ).hexdigest()
    return f"t={timestamp},v1={signature}"


def verify_signature(
    raw_body: bytes,
    header: str | None,
    secret: str,
    *,
    now: int | None = None,
    tolerance_s: int = 300,
) -> None:
    """Raise :class:`InvalidSignature` unless the header is valid and fresh."""
    if not header:
        raise InvalidSignature("Missing X-Skylink-Signature header")
    parts: dict[str, str] = {}
    for item in header.split(","):
        key, separator, value = item.partition("=")
        if separator:
            parts[key.strip()] = value.strip()
    try:
        timestamp = int(parts["t"])
        provided = parts["v1"]
    except (KeyError, ValueError) as exc:
        raise InvalidSignature("Malformed X-Skylink-Signature header") from exc
    current = now if now is not None else int(time.time())
    if abs(current - timestamp) > tolerance_s:
        raise InvalidSignature("Webhook timestamp outside tolerance window")
    expected = sign_body(secret, raw_body, timestamp).split("v1=", 1)[1]
    if not hmac.compare_digest(expected, provided):
        raise InvalidSignature("Webhook signature mismatch")
