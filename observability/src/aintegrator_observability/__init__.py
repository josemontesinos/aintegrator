"""Redacted tracing primitives shared by every AIntegrator layer (phase 7)."""

from __future__ import annotations

from aintegrator_observability.cost_report import (
    Bucket,
    CostReport,
    build_report,
    load_report,
    render_markdown,
)
from aintegrator_observability.tracing import (
    NoopSpan,
    NoopTracer,
    OtelTracer,
    Span,
    Tracer,
    configure_tracing,
    current_run_id,
    get_tracer,
    new_run_id,
    reset_run_id,
    safe_attributes,
    set_run_id,
    trace_headers,
    traced,
)

__all__ = [
    "Bucket",
    "CostReport",
    "NoopSpan",
    "NoopTracer",
    "OtelTracer",
    "Span",
    "Tracer",
    "build_report",
    "configure_tracing",
    "current_run_id",
    "get_tracer",
    "load_report",
    "new_run_id",
    "render_markdown",
    "reset_run_id",
    "safe_attributes",
    "set_run_id",
    "trace_headers",
    "traced",
]

__version__ = "1.0.0"
