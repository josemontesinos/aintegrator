"""Tests for the redacted tracing facade."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from aintegrator_observability import (
    NoopTracer,
    OtelTracer,
    configure_tracing,
    current_run_id,
    reset_run_id,
    safe_attributes,
    set_run_id,
    trace_headers,
    traced,
)


def test_safe_attributes_redacts_secrets_and_bounds_strings() -> None:
    attrs = safe_attributes(
        {
            "tool": "create_order",
            "api_key": "never-export-me",
            "password": "never-export-me",
            "token": "never-export-me",
            "SKYLINK_WEBHOOK_SECRET": "never-export-me",
            "none": None,
            "long": "x" * 500,
            "tokens": ["a", "b"],
            "steps": 3,
            "ok": True,
        }
    )
    assert attrs["tool"] == "create_order"
    assert attrs["steps"] == 3
    assert attrs["ok"] is True
    assert attrs["tokens"] == "a,b"
    assert attrs["long"].endswith("...")
    assert len(attrs["long"]) == 256
    assert not any("never-export-me" in str(value) for value in attrs.values())
    assert "api_key" not in attrs
    assert "password" not in attrs
    assert "token" not in attrs
    assert "SKYLINK_WEBHOOK_SECRET" not in attrs
    assert "none" not in attrs


def test_noop_tracer_and_run_id_context() -> None:
    tracer = NoopTracer()
    with tracer.start_span("agent.run", {"run_id": "r-1"}) as span:
        span.set_attribute("steps", 1)
        span.set_status("error", "boom")
        span.record_exception(ValueError("boom"))
    token = set_run_id("r-2")
    try:
        assert current_run_id() == "r-2"
    finally:
        reset_run_id(token)
    assert current_run_id() is None


class _RecordingSpan:
    def __init__(self) -> None:
        self.attributes: dict[str, object] = {}
        self.status: str | None = None
        self.exceptions: list[BaseException] = []

    def set_attribute(self, key: str, value: object) -> None:
        self.attributes[key] = value

    def record_exception(self, exception: BaseException) -> None:
        self.exceptions.append(exception)

    def set_status(self, status: str, description: str | None = None) -> None:
        self.status = status


class _RecordingTracer:
    def __init__(self) -> None:
        self.names: list[str] = []
        self.spans: list[_RecordingSpan] = []

    @contextmanager
    def start_span(self, name: str, attributes: Any = None) -> Iterator[_RecordingSpan]:
        span = _RecordingSpan()
        self.names.append(name)
        self.spans.append(span)
        if attributes:
            span.attributes.update(attributes)
        yield span

    def start_as_current_span(self, name: str) -> Any:
        return self.start_span(name)


class _Service:
    def __init__(self, tracer: Any) -> None:
        self.tracer = tracer

    @traced("integration.check_eligibility", attributes=lambda self, n: {"providers": n})
    async def check(self, n: int) -> int:
        return n * 2

    @traced("integration.failure")
    async def fail(self) -> None:
        raise RuntimeError("fictional failure")


async def test_traced_decorator_records_success_and_failure() -> None:
    tracer = _RecordingTracer()
    service = _Service(tracer)
    assert await service.check(3) == 6
    assert tracer.names == ["integration.check_eligibility"]
    assert tracer.spans[0].attributes["providers"] == 3
    assert tracer.spans[0].status == "ok"

    try:
        await service.fail()
    except RuntimeError:
        pass
    else:
        raise AssertionError("decorated method must propagate exceptions")
    assert tracer.spans[1].status == "error"
    assert isinstance(tracer.spans[1].exceptions[0], RuntimeError)


async def test_otel_tracer_uses_injected_tracer_and_safe_attributes() -> None:
    tracer = _RecordingTracer()
    wrapped = OtelTracer("fictional-service", tracer=tracer)
    with wrapped.start_span("agent.llm", {"input_tokens": 12, "api_key": "x"}) as span:
        span.set_status("ok")
    assert tracer.names == ["agent.llm"]
    assert tracer.spans[0].attributes == {"input_tokens": 12}
    assert tracer.spans[0].status is not None


def test_configure_tracing_defaults_to_offline(monkeypatch: Any) -> None:
    monkeypatch.delenv("OTEL_ENABLED", raising=False)
    assert isinstance(configure_tracing(), NoopTracer)
    monkeypatch.setenv("OTEL_ENABLED", "false")
    assert isinstance(configure_tracing(enabled=None), NoopTracer)
    assert trace_headers() == {}
