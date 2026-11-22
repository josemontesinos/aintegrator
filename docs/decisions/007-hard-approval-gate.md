# 007 — Hard, parameter-bound approval gate for write tools

- Status: accepted
- Date: 2026-11-22
- Phase: 4

## Context

Phases 1–3 asked the model to request confirmation before `create_order` or `cancel_order`.
That is a UX convention, not a control: a prompt-injected or confused model can call a write tool
directly, and the MCP server accepts any well-formed call. The CLI's `--approve` flag was parsed
but ignored. We need a control that does not depend on the model's goodwill and that works for
every executor (MCP client, in-process fallback or a future HTTP transport).

## Decision

Move all LLM-facing policy into `integration.security` and route every tool call through
`integration.tooling.ToolOperations.execute`, which:

1. checks the active **tool profile** allow-list;
2. audits the call (redacted arguments hash, result code, latency, approval ID);
3. for writes, calls `ApprovalService.require(action, params)`, which creates a DB-backed
   `PENDING` approval on the first call and raises `APPROVAL_REQUIRED` with the exact
   `call_arguments` the caller must repeat;
4. on retry, atomically consumes a `GRANTED` token only if it is unexpired and its SHA-256
   parameter hash matches the action and parameters.

Approvals are **single-use**, **parameter-bound**, **expiring** (`APPROVAL_TTL_S`, default 300 s)
and stored in the existing `approvals` table. Grant and consume are conditional `UPDATE`s, so
concurrent callers cannot double-use a token. A human grants via `aintegrator approvals approve
<id>`; the agent wrapper `ApprovalPolicyExecutor` implements `--approve never|ask|auto` by
prompting or auto-granting and then retrying with the *bound* arguments. It never grants a token
inside the model's tool result.

The same policy object is injected into the FastMCP server and the in-process fallback, so there
is no executor-specific bypass. Per-order and 30-day spend caps and the write rate limit remain in
`ConnectivityService`, below the gate (defense in depth).

## Alternatives considered

- Trust the system prompt and add more confirmation wording — rejected: not a security boundary.
- Keep approval state in the agent process — rejected: it would not survive restarts and would
  let a second server instance bypass it.
- Grant approvals from the tool result without a human — rejected: that is what we are defending
  against.

## Consequences

A write now requires two tool calls plus a human action. Agent traces and the audit log show the
`APPROVAL_REQUIRED` step. Tool descriptions tell the model to expect an approval ID. The read-only
profile hides write tools entirely at both the schema and execution layers. The approval stored
parameters include generated `idempotency_key` and `client_reference`, so retries must use the
`call_arguments` returned in the approval error (the wrapper does this automatically).
