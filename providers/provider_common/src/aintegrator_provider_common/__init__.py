"""Shared, framework-independent primitives for the simulated providers.

Fault decisions are pure functions of a seed, an operation name and a call index,
which makes every injected failure reproducible in tests and evals.
"""

from aintegrator_provider_common.determinism import digest_int, pick, rate_from_hash, seeded_hash
from aintegrator_provider_common.effects import (
    error_status,
    latency_seconds,
    retry_after_seconds,
    should_hang,
)
from aintegrator_provider_common.faults import (
    FaultDecision,
    FaultInjector,
    FaultKind,
    FaultProfile,
    FaultRule,
)
from aintegrator_provider_common.webhooks import DeliveryPlan, WebhookEnvelope, apply_delivery_fault

__all__ = [
    "DeliveryPlan",
    "FaultDecision",
    "FaultInjector",
    "FaultKind",
    "FaultProfile",
    "FaultRule",
    "WebhookEnvelope",
    "__version__",
    "apply_delivery_fault",
    "digest_int",
    "error_status",
    "latency_seconds",
    "pick",
    "rate_from_hash",
    "retry_after_seconds",
    "seeded_hash",
    "should_hang",
]

__version__ = "0.1.0"
