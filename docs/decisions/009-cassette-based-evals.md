# 009 — Deterministic, state-based evals with checked-in cassettes

- Status: accepted
- Date: 2026-11-22
- Phase: 5

## Context

The agent's behaviour depends on an external LLM. Calling a live model in CI makes failures hard
to reproduce: model revisions, sampling and provider latency turn a red build into noise. At the
same time, judging only the final answer is weak — an agent can write the right words while
creating the wrong order, or place two orders for one request. Phase 2 introduced `AgentResult`
traces and phase 4 introduced the audit log, so there is enough observable state to grade
correctness without trusting prose.

## Decision

The phase 5 suite stores 34 YAML cases with **checked-in LLM cassettes**. Each case names a
provider scenario, a tool profile, an approval policy, a fixed reply sequence and deterministic
expectations. Each case runs three times against a fresh in-memory SQLite database with fresh
provider replicas and a fresh cassette. Explicit `idempotency_key` and `client_reference` values
are required in write calls so the tool boundary does not generate random UUIDs.

Graders prioritise **state** (orders, statuses, audit rows, approvals), then **trajectory** (exact
agent-level tool sequence and forbidden tools), then **answer rubrics**. Rubrics use an offline
keyword judge by default; an optional `PinnedLLMJudge` uses `claude-sonnet-5-5` at temperature 0
with a JSON-only prompt and is advisory only. `make eval` exits non-zero on any failed case or any
drift in the stable metrics stored in `evals/baseline.json`; latency is reported but excluded from
the gate because it is machine-dependent. Determinism is measured by comparing normalised
fingerprints across the three repeats.

## Alternatives considered

- **Live LLM as the CI gate** — rejected: non-reproducible and costly; kept as an optional manual
  smoke test via `EVAL_LLM_API_KEY`.
- **LLM-as-judge as the primary signal** — rejected: it cannot see database state and is itself
  non-deterministic.
- **Snapshotting full tool JSON including UUIDs** — rejected: random order/approval IDs would
  make every repeat look different; they are normalised out instead.
- **One run per case** — rejected: N=3 catches hidden state leakage and accidental randomness.

## Consequences

The suite needs no API key, no Docker stack and finishes in a few seconds, so it runs in every CI
build. Adding or changing a case requires a deliberate `make eval-baseline` commit, which makes
behaviour changes reviewable. The cassettes test the agent's **decision loop** against a fixed
model transcript, not the model's ability to generate that transcript; prompt/model quality must be
checked separately with a live-model smoke test.
