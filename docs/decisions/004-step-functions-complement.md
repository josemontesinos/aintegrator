# 004 — Step Functions and the reconciler

- Status: accepted
- Date: 2026-11-22
- Phase: 0

## Context

Section 17 asks whether an AWS Step Functions order-lifecycle state machine replaces or
complements the DB-driven reconciler. The reconciler is required to converge even if every
webhook is dropped, including in local development where AWS is absent.

## Decision

Step Functions **complements** the reconciler, per the spec default. The DB-driven reconciler is
the authoritative, deployment-independent convergence mechanism; the state machine uses task
tokens with timeouts to handle the common path and to demonstrate the AWS integration.

## Alternatives considered

- Step Functions as the only convergence path — rejected: local runs and CI would lose the
  guarantee that orders converge when webhooks are dropped.

## Consequences

Two mechanisms must be kept consistent with the same order state machine; phase 7 documents the
trade-off and keeps the reconciler testable without AWS.
