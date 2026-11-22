# 006 — Pin the MCP SDK to the 1.x line

- Status: accepted
- Date: 2026-11-22
- Phase: 3

## Context

The repository declared `mcp>=1.0`, which resolved to MCP 2.3.0. That release renamed `FastMCP`
to `MCPServer`, removed the in-memory client/server test helper, and changed the server API. The
specification and ADR 003 describe a `FastMCP` server with stdio transport, and in-memory protocol
tests are valuable for keeping unit tests free of subprocesses and services.

## Decision

Constrain `mcp>=1.0,<2` in both `mcp_server/pyproject.toml` and `agent/pyproject.toml`. The agent
uses the `mcp` client SDK only and never imports the server package.

## Alternatives considered

- Migrate to MCP 2.x `MCPServer` — rejected for this phase: it would require reworking the in-memory
  test strategy and diverges from the specified FastMCP surface without adding user value.
- Leave the constraint open and support both APIs — rejected: two code paths double the test matrix
  for no functional gain.

## Consequences

Upgrading to MCP 2.x is a deliberate future change: rename the server class and replace the
in-memory tests with an equivalent transport. The lockfile currently pins 1.30.0.
