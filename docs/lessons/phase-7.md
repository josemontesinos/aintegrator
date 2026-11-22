# Phase 7 lessons — deployment, tracing and cost

- **A protocol beats an SDK dependency.** A four-method `Tracer` protocol let the agent,
  integration, webhook and reconciler emit spans without any of them importing OpenTelemetry. The
  only SDK import lives inside `observability`, guarded by `OTEL_ENABLED`.
- **Redaction belongs at the attribute boundary.** Filtering names containing token/secret/password
  and truncating strings is cheap defence in depth; tool arguments and results still never become
  span attributes. The redactor remains the authoritative scrubber for audit and model-visible data.
- **Run IDs are context, not parameters.** A `ContextVar` set once in `AgentLoop.run` correlated
  agent, tool, service and HTTP spans without threading a `run_id` through every signature. The
  `finally` reset matters: a leaked context would silently join unrelated runs.
- **Only propagate headers when a span is live.** `trace_headers()` returns `{}` for the default
  no-op path. Outbound HTTP therefore stays byte-identical in evals and contract tests.
- **Latency broke cost-report determinism.** The first JSON report differed only in p50/p95 on
  repeat runs. Removing latency from the checked-in input (while keeping it in the CLI summary)
  made the report byte-identical and the CI drift check meaningful.
- **Terraform has small, sharp syntax details.** `for`-expressions inside `jsonencode` are the valid
  substitute for `dynamic "secrets"` blocks in an ECS container definition; the ADOT exporter is
  named `awsxray`; Step Functions task tokens come from `$$.Task.Token`, not `$$.TaskToken`. Real
  `terraform validate` caught nothing once static tests were fixed, but only because the CLI was
  available locally before merge.
- **Static tests must ignore generated directories.** Provider binaries downloaded into
  `.terraform/` broke a naive `rglob("*")` secret scan. Restricting scans to text suffixes and
  skipping dot-directories keeps the check useful without depending on Terraform being installed.
- **Deployment docs are part of the control.** Saying "ARNs only, all-zero demo account, public
  HTTP is demo-only" in the module README, ADR and top-level docs prevents a fictional example from
  being copied into a real account by accident.
