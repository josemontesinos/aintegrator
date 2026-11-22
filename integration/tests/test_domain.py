"""Unit tests for the state machine and untrusted-text sanitisation."""

import pytest
from aintegrator_integration.domain import (
    OrderStatus,
    ProviderProtocolError,
    can_apply,
    sanitize_untrusted_text,
    state_rank,
)


@pytest.mark.parametrize(
    ("current", "incoming", "expected"),
    [
        (OrderStatus.PENDING_SUBMISSION, OrderStatus.SUBMITTED, True),
        (OrderStatus.SUBMITTED, OrderStatus.ACCEPTED, True),
        (OrderStatus.ACCEPTED, OrderStatus.SUBMITTED, False),
        (OrderStatus.IN_PROGRESS, OrderStatus.COMPLETED, True),
        (OrderStatus.COMPLETED, OrderStatus.CANCELLED, False),
        (OrderStatus.PENDING_SUBMISSION, OrderStatus.NEEDS_REVIEW, True),
        (OrderStatus.NEEDS_REVIEW, OrderStatus.SUBMITTED, True),
        (OrderStatus.NEEDS_REVIEW, OrderStatus.NEEDS_REVIEW, False),
    ],
)
def test_state_machine(current: OrderStatus, incoming: OrderStatus, expected: bool) -> None:
    assert can_apply(current, incoming) is expected


def test_terminal_rank_and_unknown() -> None:
    assert state_rank(OrderStatus.IN_PROGRESS) == 3
    assert state_rank(OrderStatus.COMPLETED) == 4


def test_untrusted_text_is_sanitised() -> None:
    assert sanitize_untrusted_text("  hello\x00 world \n") == "hello world"
    assert sanitize_untrusted_text("x" * 600) is not None
    assert len(sanitize_untrusted_text("x" * 600) or "") == 500
    assert sanitize_untrusted_text("   ") is None


def test_error_contract() -> None:
    error = ProviderProtocolError("bad xml")
    assert error.code == "PROVIDER_PROTOCOL_ERROR"
    assert error.retryable is False
    assert error.as_dict()["message"] == "bad xml"
