"""Small tracing facade: redacted attributes, run IDs and optional OTLP export.

The rest of the workspace depends only on the ``Tracer`` protocol and this module,
never on a concrete SDK. With ``OTEL_ENABLED=false`` (the default) every span is a
no-op, so unit tests and evals stay deterministic and offline.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager, nullcontext
from contextvars import ContextVar, Token
from functools import wraps
from typing import Any, ParamSpec, Protocol, TypeVar, cast

__all__ = [
    "NoopSpan",
    "NoopTracer",
    "OtelTracer",
    "Span",
    "Tracer",
    "configure_tracing",
    "current_run_id",
    "get_tracer",
    "new_run_id",
    "reset_run_id",
    "safe_attributes",
    "set_run_id",
    "trace_headers",
    "traced",
]

_SENSITIVE_PARTS = ("password", "passwd", "secret", "authorization", "credential", "api_key")
_SENSITIVE_EXACT = {"token", "access_token", "refresh_token", "id_token"}
_MAX_ATTRIBUTE_CHARS = 256
_run_id: ContextVar[str | None] = ContextVar("aintegrator_run_id", default=None)
P = ParamSpec("P")
R = TypeVar("R")
_configured: Tracer | None = None


def new_run_id() -> str:
    """Return a fresh correlation ID for one agent/tool execution."""
    from uuid import uuid4

    return str(uuid4())


def set_run_id(run_id: str | None) -> Token[str | None]:
    """Bind a run ID to the current context; reset it with :func:`reset_run_id`."""
    return _run_id.set(run_id)


def reset_run_id(token: Token[str | None]) -> None:
    """Restore the run ID that was active before :func:`set_run_id`."""
    _run_id.reset(token)


def current_run_id() -> str | None:
    """Return the run ID visible to the current span, if any."""
    return _run_id.get()


def safe_attributes(attributes: Mapping[str, object]) -> dict[str, object]:
    """Keep span attributes primitive, bounded and free of obvious secrets.

    Callers still choose attribute names carefully: this is defence in depth, not
    a substitute for the ``Redactor`` used on tool payloads and audit rows.
    """
    result: dict[str, object] = {}
    for key, value in attributes.items():
        lowered = key.lower()
        if value is None or lowered in _SENSITIVE_EXACT:
            continue
        if any(part in lowered for part in _SENSITIVE_PARTS):
            continue
        if isinstance(value, bool | int | float):
            result[key] = value
        elif isinstance(value, str):
            result[key] = _truncate(value)
        elif isinstance(value, (list, tuple)):
            result[key] = _truncate(",".join(str(item) for item in value))
    return result


def _truncate(value: str) -> str:
    if len(value) <= _MAX_ATTRIBUTE_CHARS:
        return value
    return value[: _MAX_ATTRIBUTE_CHARS - 3] + "..."


class Span(Protocol):
    """The tiny subset of an OpenTelemetry span that callers may use."""

    def set_attribute(self, key: str, value: object) -> None: ...

    def record_exception(self, exception: BaseException) -> None: ...

    def set_status(self, status: str, description: str | None = None) -> None: ...


class Tracer(Protocol):
    """Start a span around an operation; no SDK required."""

    def start_span(
        self, name: str, attributes: Mapping[str, object] | None = None
    ) -> AbstractContextManager[Span]: ...


class NoopSpan:
    """A span that records nothing."""

    def set_attribute(self, key: str, value: object) -> None:
        return None

    def record_exception(self, exception: BaseException) -> None:
        return None

    def set_status(self, status: str, description: str | None = None) -> None:
        return None


class NoopTracer:
    """Default tracer for offline tests, evals and disabled tracing."""

    def start_span(
        self, name: str, attributes: Mapping[str, object] | None = None
    ) -> AbstractContextManager[Span]:
        return nullcontext(NoopSpan())


class _SdkSpan:
    """Adapter over a real OpenTelemetry span; keeps SDK imports out of callers."""

    def __init__(self, span: Any) -> None:
        self._span = span

    def set_attribute(self, key: str, value: object) -> None:
        self._span.set_attribute(key, value)

    def record_exception(self, exception: BaseException) -> None:
        self._span.record_exception(exception)

    def set_status(self, status: str, description: str | None = None) -> None:
        from opentelemetry.trace import Status, StatusCode

        code = StatusCode.ERROR if status == "error" else StatusCode.OK
        self._span.set_status(Status(code, description))


class OtelTracer:
    """Use an injected tracer or the SDK's active tracer when available."""

    def __init__(self, service_name: str = "aintegrator", tracer: Any | None = None) -> None:
        self._service_name = service_name
        self._tracer = tracer

    def _active(self) -> Any:
        if self._tracer is None:
            from opentelemetry import trace

            self._tracer = trace.get_tracer(self._service_name)
        return self._tracer

    @contextmanager
    def start_span(
        self, name: str, attributes: Mapping[str, object] | None = None
    ) -> Iterator[Span]:
        with self._active().start_as_current_span(name) as raw_span:
            span = _SdkSpan(raw_span)
            for key, value in safe_attributes(attributes or {}).items():
                span.set_attribute(key, value)
            yield span


def get_tracer() -> Tracer:
    """Return the configured tracer, or a no-op before ``configure_tracing``."""
    return _configured or NoopTracer()


def trace_headers() -> dict[str, str]:
    """Return a W3C ``traceparent`` for outbound HTTP calls, when tracing is live."""
    try:
        from opentelemetry.trace import get_current_span

        context = get_current_span().get_span_context()
        if not context.is_valid:
            return {}
        return {
            "traceparent": (
                f"00-{context.trace_id:032x}-{context.span_id:016x}-{context.trace_flags:02x}"
            )
        }
    except Exception:  # pragma: no cover - the API is optional at runtime
        return {}


def configure_tracing(
    service_name: str | None = None,
    *,
    endpoint: str | None = None,
    enabled: bool | None = None,
) -> Tracer:
    """Configure OTLP export once and return the process-wide tracer.

    ``OTEL_ENABLED`` defaults to false so a bare checkout never opens a network
    connection. Deployment sets it to true and supplies
    ``OTEL_EXPORTER_OTLP_ENDPOINT``; without the ``otlp`` extra the API is still
    used, but spans stay local no-ops.
    """
    global _configured
    name = service_name or os.getenv("OTEL_SERVICE_NAME") or "aintegrator"
    if enabled is None:
        enabled = os.getenv("OTEL_ENABLED", "false").strip().lower() not in {
            "0",
            "false",
            "no",
            "off",
        }
    if not enabled:
        return NoopTracer()
    if _configured is not None:
        return _configured

    target = endpoint or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        _configured = OtelTracer(name)
        return _configured

    provider = TracerProvider(resource=Resource.create({"service.name": name}))
    if target:
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=target)))
    trace.set_tracer_provider(provider)
    _configured = OtelTracer(name)
    return _configured


def traced(
    operation: str,
    *,
    attributes: Callable[..., dict[str, object]] | None = None,
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """Decorate an async method with a span that ends even when it raises."""

    def decorate(func: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            owner = args[0] if args else None
            tracer = cast(Tracer, getattr(owner, "tracer", None) or get_tracer())
            extra = attributes(*args, **kwargs) if attributes is not None else {}
            values = safe_attributes(extra)
            run_id = current_run_id()
            if run_id:
                values["run_id"] = run_id
            with tracer.start_span(operation, values) as span:
                try:
                    result = await func(*args, **kwargs)
                except Exception as exc:
                    span.record_exception(exc)
                    span.set_status("error", type(exc).__name__)
                    raise
                span.set_status("ok")
                return result

        return wrapper

    return decorate
