# Phase 4 lessons — security controls

- **Put the gate below the transport, not inside the prompt.** Moving approvals, profiles,
  redaction and audit into `integration.security` and routing both the MCP server and the
  in-process fallback through `ToolOperations.execute` meant one implementation and no
  executor-specific bypass. The agent can be replaced without re-reviewing the policy.
- **Generated values must be part of the approval.** `idempotency_key` and `client_reference` are
  created at request time. Binding them into the approval hash prevents a retry from quietly
  creating a *second* order; returning `call_arguments` in the error gives the wrapper the exact
  payload to replay. Tests should use those arguments rather than re-sending the model's original
  call.
- **"Single-use" needs a conditional UPDATE.** A read-then-write status flip is racy. Both grant
  (`PENDING -> GRANTED`) and consume (`GRANTED -> CONSUMED`) are now `UPDATE ... WHERE status = ?`
  and check the affected row count.
- **SQLite drops timezone info.** `DateTime(timezone=True)` comes back naive from aiosqlite. A
  small `_aware()` helper in both the repository and the approval service keeps expiry checks
  working across Postgres and the in-memory test database.
- **Least privilege is more than a config string.** A separate role with no `DELETE`/`TRUNCATE`
  and only `INSERT` on `audit_log` turns the audit trail into something the tool executor cannot
  erase. Creating the role both in the init script and idempotently in the migration avoids a
  broken stack when an existing volume is reused.
- **Redaction has two surfaces.** Tool traces shown to the model/user and the persistent audit log
  need the same redactor; otherwise a secret removed from one leaks through the other. Hashing
  redacted arguments (not raw ones) keeps `params_hash` clean too.
- **Hiding a tool is not disabling it.** A read-only profile removes write tools from
  `list_tools`, but the server still re-checks the allow-list in `execute`. Defence in depth:
  `--approve never` blocks writes in the CLI wrapper as a third layer.
- **Tests caught the seams.** Existing lifecycle tests failed immediately once the gate was added,
  which was useful: they now exercise the full "request → grant → retry" path over both MCP and
  in-process transports, so a future refactor cannot silently drop the approval step.
