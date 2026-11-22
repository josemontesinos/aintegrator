"""Order state machine: events only advance rank, never regress it.

``NEEDS_REVIEW`` is a side state for ambiguous provider outcomes; reconciliation
is the only thing that resolves it. Every incoming event is still recorded by
the caller, even when it does not change the state.
"""

from __future__ import annotations

from aintegrator_integration.domain.model import OrderStatus

__all__ = ["RANK", "TERMINAL_STATUSES", "can_apply", "state_rank"]

RANK: dict[OrderStatus, int] = {
    OrderStatus.PENDING_SUBMISSION: 0,
    OrderStatus.SUBMITTED: 1,
    OrderStatus.ACCEPTED: 2,
    OrderStatus.IN_PROGRESS: 3,
    OrderStatus.COMPLETED: 4,
    OrderStatus.REJECTED: 4,
    OrderStatus.CANCELLED: 4,
    OrderStatus.FAILED: 4,
}

TERMINAL_STATUSES = frozenset(
    {
        OrderStatus.COMPLETED,
        OrderStatus.REJECTED,
        OrderStatus.CANCELLED,
        OrderStatus.FAILED,
    }
)

_REVIEW_RESOLUTIONS = frozenset(
    {
        OrderStatus.SUBMITTED,
        OrderStatus.ACCEPTED,
        OrderStatus.IN_PROGRESS,
        *TERMINAL_STATUSES,
    }
)


def state_rank(status: OrderStatus) -> int | None:
    return RANK.get(status)


def can_apply(current: OrderStatus, incoming: OrderStatus) -> bool:
    """Return whether ``incoming`` should become the new order status."""
    if current is OrderStatus.NEEDS_REVIEW:
        return incoming in _REVIEW_RESOLUTIONS
    if incoming is OrderStatus.NEEDS_REVIEW:
        return current in (OrderStatus.PENDING_SUBMISSION, OrderStatus.SUBMITTED)
    current_rank = RANK.get(current)
    incoming_rank = RANK.get(incoming)
    if current_rank is None or incoming_rank is None:
        return False
    return incoming_rank > current_rank
