"""Pure, framework-independent fault injection.

Decisions are a deterministic function of ``(seed, operation, call_index, kind)`` so
that fault scenarios are reproducible in tests and evals. No web framework, clock
or I/O is used here.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

__all__ = [
    "FaultDecision",
    "FaultInjector",
    "FaultKind",
    "FaultProfile",
    "FaultRule",
]


class FaultKind(StrEnum):
    """All fault kinds supported by the simulated providers."""

    LATENCY = "latency"
    HTTP_ERROR = "http_error"
    TIMEOUT = "timeout"
    PARTIAL_RESPONSE = "partial_response"
    MALFORMED_RESPONSE = "malformed_response"
    RATE_LIMIT = "rate_limit"
    TOKEN_EXPIRY = "token_expiry"
    WEBHOOK_DUPLICATE = "webhook_duplicate"
    WEBHOOK_REORDER = "webhook_reorder"
    WEBHOOK_DROP = "webhook_drop"
    WEBHOOK_DELAY = "webhook_delay"


class FaultRule(BaseModel):
    """One probabilistic rule. ``operation`` is an exact name or ``*`` for all."""

    operation: str = "*"
    kind: FaultKind
    probability: float = Field(ge=0.0, le=1.0)
    params: dict[str, Any] = Field(default_factory=dict)


class FaultProfile(BaseModel):
    """A complete fault configuration for one provider."""

    seed: int = 0
    rules: list[FaultRule] = Field(default_factory=list)


@dataclass(frozen=True, slots=True)
class FaultDecision:
    """The single rule that fired for a call, with the deterministic roll."""

    kind: FaultKind
    params: dict[str, Any]
    operation: str
    call_index: int
    roll: int


def _roll(seed: int, operation: str, call_index: int, kind: FaultKind) -> int:
    payload = f"{seed}:{operation}:{call_index}:{kind.value}".encode()
    return int.from_bytes(hashlib.sha256(payload).digest()[:4], "big")


class FaultInjector:
    """Stateful wrapper around a :class:`FaultProfile` with per-operation counters.

    ``decide`` advances the call counter for the operation. ``peek`` does not, which
    is useful for control endpoints and diagnostics.
    """

    def __init__(self, profile: FaultProfile | None = None) -> None:
        self._profile = profile or FaultProfile()
        self._counts: dict[str, int] = {}

    @property
    def profile(self) -> FaultProfile:
        return self._profile

    def set_profile(self, profile: FaultProfile) -> None:
        """Replace the profile and reset all call counters."""
        self._profile = profile
        self._counts.clear()

    def reset(self) -> None:
        """Restore an empty profile and reset all call counters."""
        self._profile = FaultProfile()
        self._counts.clear()

    def call_index(self, operation: str) -> int:
        return self._counts.get(operation, 0)

    def decide(self, operation: str, call_index: int | None = None) -> FaultDecision | None:
        """Return the fault that should affect this call, or ``None``."""
        index = self.call_index(operation) if call_index is None else call_index
        if call_index is None:
            self._counts[operation] = index + 1
        return self._decide(operation, index)

    def peek(self, operation: str, call_index: int | None = None) -> FaultDecision | None:
        """Decide without advancing the counter."""
        index = self.call_index(operation) if call_index is None else call_index
        return self._decide(operation, index)

    def _decide(self, operation: str, call_index: int) -> FaultDecision | None:
        for rule in self._profile.rules:
            if rule.operation not in ("*", operation):
                continue
            roll = _roll(self._profile.seed, operation, call_index, rule.kind)
            if roll < int(rule.probability * 2**32):
                return FaultDecision(
                    kind=rule.kind,
                    params=dict(rule.params),
                    operation=operation,
                    call_index=call_index,
                    roll=roll,
                )
        return None
