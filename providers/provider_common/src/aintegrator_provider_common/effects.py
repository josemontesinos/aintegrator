"""Small helpers translating a :class:`FaultDecision` into concrete effects."""

from __future__ import annotations

from aintegrator_provider_common.faults import FaultDecision, FaultKind

__all__ = ["error_status", "latency_seconds", "retry_after_seconds", "should_hang"]


def latency_seconds(decision: FaultDecision | None) -> float:
    """Fixed latency plus a deterministic jitter derived from the decision roll."""
    if decision is None or decision.kind is not FaultKind.LATENCY:
        return 0.0
    fixed_ms = int(decision.params.get("fixed_ms", 0))
    jitter_ms = int(decision.params.get("jitter_ms", 0))
    return (fixed_ms + (decision.roll % (jitter_ms + 1) if jitter_ms else 0)) / 1000.0


def should_hang(decision: FaultDecision | None) -> bool:
    return decision is not None and decision.kind is FaultKind.TIMEOUT


def error_status(decision: FaultDecision | None, default: int = 500) -> int:
    if decision is None or decision.kind is not FaultKind.HTTP_ERROR:
        return default
    return int(decision.params.get("status", default))


def retry_after_seconds(decision: FaultDecision | None, default: int = 1) -> int:
    if decision is None or decision.kind is not FaultKind.RATE_LIMIT:
        return default
    return int(decision.params.get("retry_after_s", default))
