# 013 — Fictional AWS/Terraform demo topology

- Status: accepted
- Date: 2026-11-22
- Phase: 7

## Context

Phase 7 must show how the local compose stack maps to AWS without using real telecom data, real
credentials or the reader's employer infrastructure. Terraform validation must run in CI where no
AWS account or state backend is available.

## Decision

Provide a self-contained module under `infra/terraform` for a fictional demo: one VPC with two
public subnets, ECR repositories, ECS Fargate services registered in a private Cloud Map namespace,
a public ALB for the webhook, an EventBridge-scheduled reconciler, an ADOT OTLP collector exporting
to X-Ray, SQS with a DLQ plus a dependency-free Lambda bridge, and a Step Functions workflow. The
state backend block is commented so `terraform init -backend=false` and `terraform validate` work in
CI. Credentials are passed only as SSM/Secrets Manager ARNs via `TF_VAR_*`; no literal secret or
real account ID appears in the repository, and the provider lock file is committed.

Public subnets, public IPs and HTTP are deliberate demo simplifications documented as unsafe for
production. Step Functions complements, never replaces, the DB reconciler (ADR 004).

## Alternatives considered

- Private subnets, VPC endpoints and TLS in the demo module — rejected for this phase: the module
  is a readability artefact and those resources would obscure the service mapping. Documented as
  the required production hardening instead.
- Passing secrets as plaintext tfvars — rejected: would violate the phase 4 threat model.
- Only static tests, no real Terraform validation — rejected: static tests cannot catch invalid HCL
  or provider schema usage; CI runs real `fmt`/`init`/`validate`.

## Consequences

Readers can map every compose service to an AWS resource and deploy the demo in an isolated account.
The module is not production-ready: hardening, TLS, image digest pinning and managed Postgres are
explicit follow-ups.
