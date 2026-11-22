"""Process-wide fault injector shared by API views, controls and delivery."""

from __future__ import annotations

from aintegrator_provider_common import FaultInjector

WEBHOOK_OPERATION = "webhook.delivery"
INBOUND_OPERATIONS = ("availability.create", "availability.get", "orders.create", "orders.get")

INJECTOR = FaultInjector()


def get_injector() -> FaultInjector:
    """Return the single injector used by this provider process."""
    return INJECTOR


def reset_runtime() -> None:
    """Clear the fault profile and all per-operation call counters."""
    INJECTOR.reset()
