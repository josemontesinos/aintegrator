"""Spans emitted by the integration layer use run IDs and redacted attributes."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from aintegrator_integration.config import Settings
from aintegrator_integration.domain import Address
from aintegrator_integration.service import ConnectivityService
from aintegrator_integration.tooling import ToolOperations
from aintegrator_observability import reset_run_id, set_run_id
from test_service import FakeGateway

_ADDRESS = {
    "street": "Fictional Street",
    "number": "12",
    "postal_code": "28001",
    "city": "Madrid",
    "country": "ES",
}


class _RecordingSpan:
    def __init__(self) -> None:
        self.attributes: dict[str, object] = {}
        self.status: str | None = None

    def set_attribute(self, key: str, value: object) -> None:
        self.attributes[key] = value

    def record_exception(self, exception: BaseException) -> None:
        del exception

    def set_status(self, status: str, description: str | None = None) -> None:
        del description
        self.status = status


class _RecordingTracer:
    def __init__(self) -> None:
        self.names: list[str] = []
        self.spans: list[_RecordingSpan] = []

    @contextmanager
    def start_span(self, name: str, attributes: Any = None) -> Iterator[_RecordingSpan]:
        span = _RecordingSpan()
        span.attributes.update(attributes or {})
        self.names.append(name)
        self.spans.append(span)
        yield span


def _service(session_maker: Any, tracer: Any) -> ConnectivityService:
    return ConnectivityService(
        {"fibernet": FakeGateway()},
        session_maker,
        settings=Settings(),
        tracer=tracer,
    )


async def test_service_span_propagates_the_run_id(session_maker: Any) -> None:
    tracer = _RecordingTracer()
    service = _service(session_maker, tracer)
    token = set_run_id("run-7")
    try:
        results = await service.check_eligibility(Address(**_ADDRESS))
    finally:
        reset_run_id(token)

    assert results
    assert tracer.names == ["integration.eligibility"]
    assert tracer.spans[0].attributes["run_id"] == "run-7"
    assert tracer.spans[0].attributes["providers"] == 1
    assert tracer.spans[0].status == "ok"


async def test_tool_span_never_exposes_tool_arguments(session_maker: Any) -> None:
    tracer = _RecordingTracer()
    service = _service(session_maker, tracer)
    operations = ToolOperations(service, run_id="mcp", tracer=tracer)

    result = await operations.execute("check_eligibility", dict(_ADDRESS))

    assert json.loads(result)["ok"] is True
    assert tracer.names == ["integration.tool", "integration.eligibility"]
    tool_span = tracer.spans[0]
    assert tool_span.attributes["tool"] == "check_eligibility"
    assert tool_span.attributes["run_id"] == "mcp"
    assert tool_span.attributes["has_approval"] is False
    assert tool_span.status == "ok"
    assert not any(_ADDRESS["city"] in str(value) for value in tool_span.attributes.values())
