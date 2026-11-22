# 012 — Deterministic cost report from eval metadata

- Status: accepted
- Date: 2026-11-22
- Phase: 7

## Context

The spec asks for a cost report grouped by tool profile and provider. Live billing APIs are
unavailable in CI, use real credentials and produce non-reproducible output. The eval suite already
replays fixed cassettes with known token counts and fictional prices, so it is a better source of
stable evidence than a live model run.

## Decision

Extend `EvalReport.as_dict()` with a top-level `config` block (model, input/output price per
million) and per-run `profile`, `provider`, `tags` and token/cost `usage`. Drop latency fields from
the JSON report so repeated offline runs are byte-identical. A CLI in `observability` reads that
report, aggregates buckets by profile and provider, and renders `docs/cost-report.md` without
timestamps, host paths or live API calls.

`COST_REPORT_DAILY_RUNS` and `COST_REPORT_DAYS` drive a clearly labelled planning projection;
`make cost-report-check` verifies the checked-in Markdown has no drift.

## Alternatives considered

- Querying a billing API — rejected: real credentials, network access and unstable numbers.
- Hard-coding cost tables in documentation — rejected: they drift from the eval configuration.
- Committing latency percentiles in the JSON input — rejected: machine-dependent bytes would make
  the cost gate flaky.

## Consequences

Cost attribution is reproducible from the same cassettes that gate behaviour. The report is an
estimate from fictional rates; it cannot answer questions about real provider spend or latency.
Changing eval cases or prices requires regenerating both the JSON report and the Markdown report.
