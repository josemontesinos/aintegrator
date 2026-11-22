# Security

**Current status (phase 4, `v0.4-security`).** Writes are gated by a server-side control that does
not depend on the model: the first `create_order`/`cancel_order` call creates a pending,
parameter-bound approval and a human must grant it before a retry can succeed. The MCP server uses
a least-privilege DB role, every tool call is audited with redacted payloads, and tool profiles can
remove writes entirely. This is a demonstrator, not a production multi-tenant service: see
[Limitations](#limitations) before drawing broader conclusions.

## Threat model

### Assets

- **Order state** in PostgreSQL: offers, orders, idempotency keys and lifecycle events.
- **Audit log**: the evidence of what the agent did and why; worthless if the tool executor can
  edit it.
- **Secrets**: provider client secrets/API keys, webhook HMAC secret, LLM API key.
- **User trust**: the agent must not claim a write succeeded when it did not.

### Actors and abuse cases

| Actor | Abuse | Mitigation |
| --- | --- | --- |
| Prompt-injected provider text | Trick the model into creating/cancelling an order | Provider text is sanitised and labelled `untrusted_text`; writes require a human-approved, parameter-bound token (ADR 007) |
| Confused or hallucinating model | Place a duplicate or wrong order | Tools re-fetch offers by `provider`+`offer_id`; generated `idempotency_key` and `client_reference` are bound into the approval; `ConnectivityService` enforces per-order and 30-day spend caps plus a write rate limit |
| Malicious tool caller | Replay an approval for different parameters | SHA-256 hash over the exact action + parameters; conditional `UPDATE` makes grant/consume single-use under concurrency |
| Curious MCP process | Delete or rewrite orders/audit rows | `aintegrator_mcp` role has no `DELETE`/`TRUNCATE`/DDL and only `INSERT` on `audit_log` (ADR 008) |
| Accidental secret leak | Credentials in traces, logs or git | `Redactor` scrubs configured secrets and credential-shaped strings before traces/audit; gitleaks runs in CI; `.env` is never committed |
| Over-privileged automation | Let the agent write in a review-only deployment | `MCP_TOOL_PROFILE=read-only` hides write tools at the schema and execution layers; `--approve never` blocks them in the CLI as well |

## Hard controls (do not rely on the model)

1. **Approval gate.** `ApprovalService` stores `PENDING` rows in `approvals`. The first write
   raises `APPROVAL_REQUIRED` with an approval ID, a human-readable summary and the exact
   `call_arguments`. A human grants with `aintegrator approvals approve <id>` (or the agent wrapper
   asks/auto-grants according to `--approve`). The retry must present the same ID and the same
   parameters. Tokens expire after `APPROVAL_TTL_S` (default 300 s), are bound to one action and
   parameter hash, and are consumed exactly once via conditional `UPDATE`.
2. **Tool profiles.** `standard` exposes all five tools; `read-only` exposes only eligibility and
   read tools. `ToolOperations.execute` re-checks the allow-list on every call, so hiding a tool
   from `list_tools` is not the only protection.
3. **Redaction and audit.** Every tool call writes one `audit_log` row with a hash of the redacted
   arguments, the result code, duration and approval ID. The agent loop redacts tool arguments,
   results and LLM-facing tool messages using the same `Redactor`. Provider free text is capped and
   stripped of control characters by `sanitize_untrusted_text`.
4. **Spend and rate limits.** `ConnectivityService` rejects orders above `max_order_value_eur`,
   enforces a rolling 30-day `max_period_value_eur` cap, and throttles writes to
   `write_rate_limit_per_minute`.
5. **Least privilege.** The MCP server connects with `MCP_DATABASE_URL` as `aintegrator_mcp`;
   admin/reconciliation paths keep the owner role. See ADR 008.
6. **Supply-chain hygiene.** `gitleaks protect` runs in CI on every push and PR; `.env.example`
   and local-only fictional credentials are explicitly allowlisted in `.gitleaks.toml`.

## Prompt-injection strategy

Provider output is data, never instructions. Adapters map it into typed DTOs; free text passes
through `sanitize_untrusted_text`, is labelled `untrusted_text` in tool results, and the MCP server
instructions plus system prompt tell the model not to follow it. Even if the model is fully
compromised, it cannot bypass items 1–5 above: the server owns the gate, not the prompt.

## Verifying the controls

```bash
make check          # includes 182 unit tests covering approvals, profiles, redaction, audit
make up
make service-test   # adapter contracts + webhook/reconciler workflows
make demo-agent     # scripted agent over MCP, no API key
make down
```

Useful manual checks with the stack up:

```bash
uv run aintegrator approvals pending
uv run aintegrator run "Cancel order <id>" --approve never   # blocked before any write call
uv run aintegrator run "Cancel order <id>" --approve ask     # prompts a human
```

## Limitations

- This is a single-user local demonstrator. Approvals are not tied to an authenticated human
  identity, and the CLI `approvals approve` command is trusted by design.
- `ALTER DEFAULT PRIVILEGES` grants broader rights on *future* tables than on current ones; review
  migration 0002 when adding tables.
- The LLM API is the only external network dependency; the simulated provider control endpoints
  are intentionally unauthenticated and must never be exposed publicly.
- Audit rows are append-only for the MCP role but not cryptographically tamper-evident. A separate
  write-only log sink would be needed for strong non-repudiation.
