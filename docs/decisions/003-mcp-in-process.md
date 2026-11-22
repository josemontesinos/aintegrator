# 003 — MCP server integration binding

- Status: accepted
- Date: 2026-11-22
- Phase: 0

## Context

Section 17 asks whether the MCP server calls `integration` in-process or over HTTP. The local
deployment is a single process for stdio transport, while streamable HTTP may run as a service.

## Decision

The MCP server imports and calls `integration` in-process, per the spec default. `integration`
remains independently callable and its service layer stays transport-agnostic, so an HTTP binding
can be added later without changing domain logic.

## Alternatives considered

- HTTP from MCP server to a separate integration service — rejected for now: extra hop, extra
  failure mode, and no benefit for the local/stdio use case.

## Consequences

Simpler local setup and cheaper tool calls. The least-privilege DB role requirement in section 9
must be applied to the MCP server process itself, since it owns the DB connection.
