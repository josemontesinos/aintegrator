# Phase 5 lessons — evals

- **State beats prose.** The strongest cases assert the order count, status and audit rows, not
  what the assistant said. A wrong-but-confident answer is caught by state; a right answer with a
  duplicate order is caught by `duplicate_order_rate`.
- **Determinism has to be engineered.** The tool boundary generates `idempotency_key` and
  `client_reference` when they are omitted, so every write cassette supplies them explicitly.
  Order and approval UUIDs are still random, so the determinism fingerprint normalises them away
  rather than pretending the whole state is byte-identical.
- **The approval retry is invisible to the trajectory.** `ApprovalPolicyExecutor` performs two
  underlying tool calls but the loop records one trace. Grading approvals from the database (one
  `CONSUMED` row, one successful audited write) tests the real gate instead of the transcript.
- **Audit order is newest-first.** `list_audit` orders by `created_at DESC`, so expected tool
  sequences must be written in that order (two `create_order` rows before `check_eligibility`).
  This surprised the first run and is now pinned by a case.
- **Even an unknown tool is profile-checked.** A cassette calling `delete_everything` returns
  `TOOL_NOT_ALLOWED` before the dispatcher can report `UNKNOWN_TOOL` — a better security outcome,
  and a reminder that the allow-list is the first gate.
- **Cassette bindings need a context.** Resolving `{{ address }}` from shared YAML fragments and
  `{{ order.order_id }}` from the last tool result with one resolver kept 34 cases short and made
  the fragments more than documentation.
- **Baselines should be narrow.** Only stable metrics gate CI; latency is reported but excluded,
  otherwise every runner would produce a false drift. N=3 repeats are cheap enough to keep as the
  default and catch per-run state leakage.
- **Unit tests for the harness itself matter.** A forgotten `await` in the grader call silently
  produced a 0% success rate with a `RuntimeWarning`; explicit tests for binding, graders and
  baseline drift would have caught it even without the full suite.
