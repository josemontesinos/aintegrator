"""Local-only control-plane operations for tests and evals.

These helpers are deliberately free of HTTP concerns; :mod:`aintegrator_fibernet.app`
exposes them on unauthenticated ``/_control`` routes.
"""

from __future__ import annotations

from typing import Any

from aintegrator_provider_common import FaultProfile

from .state import ProviderState

__all__ = ["advance_orders_now", "clear_all", "set_fault_profile", "snapshot"]


def set_fault_profile(provider_state: ProviderState, profile: FaultProfile) -> dict[str, Any]:
    """Install a fault profile and reset all per-operation call counters."""
    provider_state.injector.set_profile(profile)
    return snapshot(provider_state)


def clear_all(provider_state: ProviderState) -> dict[str, Any]:
    """Clear orders, tokens, idempotency records, rate limits and faults."""
    provider_state.reset()
    return snapshot(provider_state)


def advance_orders_now(provider_state: ProviderState) -> dict[str, int]:
    """Advance all non-terminal orders immediately and report how many changed."""
    return {"advanced": provider_state.advance_orders()}


def snapshot(provider_state: ProviderState) -> dict[str, Any]:
    """Return counts and the active fault profile."""
    return provider_state.control_snapshot()
