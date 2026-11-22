# 008 — Least-privilege database role for the MCP server

- Status: accepted
- Date: 2026-11-22
- Phase: 4

## Context

The MCP server is the process closest to untrusted tool arguments. Until phase 4 it used the same
`DATABASE_URL` as the CLI, reconciler and webhook receiver. That role can read and write every
table, so a tool-layer flaw (or a prompt-injected write) could delete orders or tamper with audit
rows. The audit log is only meaningful if the tool executor cannot rewrite or erase it.

## Decision

Introduce a separate `aintegrator_mcp` Postgres role used **only** by the MCP server, configured
through `MCP_DATABASE_URL`. Migration `0002_security_controls` creates it if needed and grants:

- `USAGE` on `public` and `SELECT` on all current and future tables;
- `INSERT, UPDATE` only on the operational tables the tools need (`orders`, `order_events`,
  `idempotency_keys`, `approvals`, `provider_calls`, `eligibility_cache`, `eligibility_requests`);
- `INSERT` (but not `UPDATE`) on `audit_log`, making audit rows append-only.

It deliberately has **no** `DELETE`, `TRUNCATE`, DDL or superuser rights. The reconciliation and
admin paths keep using the owner role. A fresh Compose volume also creates the role from
`infra/postgres/init/01-mcp-role.sql`; the password is a fictional local placeholder and is
allowlisted in gitleaks, never committed as a real secret.

## Alternatives considered

- One shared role with application-level checks — rejected: checks live in the same process an
  attacker would control.
- Separate database/schema per component — rejected for this phase: it complicates the shared
  order/approval model for little extra isolation.
- Read-only MCP role plus a write proxy — rejected: the tools must create orders and consume
  approvals, so a narrow write grant is the simpler boundary.

## Consequences

The MCP server cannot delete orders or drop tables even if every in-process check is bypassed.
Audit rows are append-only for that role (it can `INSERT` but not `UPDATE`/`DELETE` them).
`ALTER DEFAULT PRIVILEGES` is broader for *future* tables and must be reviewed when the schema
grows; deployments overriding `POSTGRES_USER` should review that clause too. Production secrets
must come from the environment or a secret manager, never from `.env.example`.
