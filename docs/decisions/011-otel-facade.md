# 011 — Tracing facade and opt-in OTLP

- Status: accepted
- Date: 2026-11-22
- Phase: 7

## Context

Phase 7 needs end-to-end traces across the CLI, agent, MCP server, integration service and HTTP
clients. The spec also requires bare checkouts, unit tests and evals to stay offline and
deterministic. Import-linter forbids providers and `rag` from importing upper layers, so tracing
must not become a new cross-layer dependency.

## Decision

Add `observability` as a leaf workspace package exposing a four-method `Tracer` protocol, a
`NoopTracer`, an `OtelTracer`, `configure_tracing`, `get_tracer`, a run-ID `ContextVar`, and a
`traced` decorator for async methods. Only `observability` imports the OpenTelemetry SDK, and only
inside functions. `OTEL_ENABLED` defaults to false; enabling it plus the optional `otlp` extra
configures a batch OTLP gRPC exporter.

Span attributes are primitive, bounded to 256 characters and filtered by sensitive name fragments;
tool arguments and results are never attributes. `AgentLoop.run` owns a fresh run ID propagated
through `ContextVar`, and outbound HTTP receives a W3C `traceparent` only when a live SDK span
exists.

## Alternatives considered

- Importing OpenTelemetry directly in every layer — rejected: couples all packages to an SDK and
  makes offline tests open network connections.
- Always-on tracing with sampling — rejected: sampling would still require exporters and make eval
  output environment-dependent.
- A tracing module inside `integration` — rejected: the agent would have to import a lower layer
  for observability and providers might follow that path.

## Consequences

Every layer may import `observability` without breaking dependency rules. Trace coverage is tested
with an in-memory recording tracer; no test requires a collector. Container images install the
`otlp` extra so the AWS deployment can set `OTEL_ENABLED=true`.
