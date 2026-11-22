"""Unit tests for deterministic fault decisions (no web framework involved)."""

from aintegrator_provider_common import (
    FaultDecision,
    FaultInjector,
    FaultKind,
    FaultProfile,
    FaultRule,
)


def test_no_rules_means_no_faults() -> None:
    injector = FaultInjector(FaultProfile(seed=1))
    assert injector.decide("coverage") is None
    assert injector.call_index("coverage") == 1


def test_decision_is_deterministic_for_same_inputs() -> None:
    profile = FaultProfile(
        seed=42,
        rules=[FaultRule(kind=FaultKind.HTTP_ERROR, probability=1.0, params={"status": 503})],
    )
    first = FaultInjector(profile).decide("orders", call_index=3)
    second = FaultInjector(profile).decide("orders", call_index=3)
    assert first == second
    assert first is not None
    assert first.params["status"] == 503
    assert first.call_index == 3


def test_operation_scoping_and_counter_advancement() -> None:
    profile = FaultProfile(
        seed=7,
        rules=[FaultRule(operation="coverage", kind=FaultKind.LATENCY, probability=1.0)],
    )
    injector = FaultInjector(profile)
    assert injector.decide("orders") is None
    assert injector.call_index("orders") == 1
    assert injector.call_index("coverage") == 0
    assert injector.decide("coverage") is not None
    assert injector.peek("coverage") is not None
    assert injector.call_index("coverage") == 1


def test_zero_probability_never_fires() -> None:
    profile = FaultProfile(
        seed=99,
        rules=[FaultRule(kind=FaultKind.TIMEOUT, probability=0.0)],
    )
    injector = FaultInjector(profile)
    assert all(injector.decide("any", call_index=i) is None for i in range(100))


def test_set_profile_resets_counters() -> None:
    injector = FaultInjector()
    injector.decide("x")
    injector.set_profile(FaultProfile(seed=5))
    assert injector.call_index("x") == 0
    assert injector.profile.seed == 5


def test_decision_is_immutable() -> None:
    decision = FaultDecision(FaultKind.LATENCY, {"fixed_ms": 1}, "op", 0, 123)
    try:
        decision.roll = 1  # type: ignore[misc]
    except AttributeError:
        return
    raise AssertionError("FaultDecision must be frozen")
