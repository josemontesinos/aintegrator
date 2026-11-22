# 005 — Default LLM model per agent profile

- Status: accepted
- Date: 2026-11-22
- Phase: 0

## Context

Section 17 asks for the default model per agent profile. The spec names `claude-sonnet-5-5` as
the default for `LLM_MODEL` and asks for model choice to be part of cost control.

## Decision

Use `claude-sonnet-5-5` from the `LLM_MODEL` environment variable as the default for the normal
agent profile and for the LLM-as-judge (pinned, temperature 0). Eval cassettes make CI
deterministic regardless of the configured model. A cheaper profile may override the model via
its own environment configuration once evals report cost per profile.

## Alternatives considered

- A large model for every profile — rejected: cost control is an explicit goal.
- Hard-coding a single model — rejected: the spec requires configuration from env.

## Consequences

Model changes are configuration-only, but eval baselines and cassettes must be regenerated when
the model changes; the judge model stays pinned so comparisons remain meaningful.
