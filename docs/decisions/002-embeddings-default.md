# 002 — Default embedding provider

- Status: accepted
- Date: 2026-11-22
- Phase: 0

## Context

Section 17 asks for the embeddings default before the RAG phase. Evals must run in CI for free and
deterministically; external embedding APIs would add cost, network dependence and nondeterminism.

## Decision

Default to the local `bge-small-en-v1.5` model through `sentence-transformers`, behind an
`Embedder` interface so a hosted provider can be swapped in via configuration. Add the heavy
dependency only in phase 6, not in the phase 0 workspace sync.

## Alternatives considered

- Hosted embeddings by default — rejected: CI evals would need a key and would no longer be free.
- No embeddings, full-text search only — rejected: the spec requires hybrid retrieval.

## Consequences

Local runs are self-contained but slow on first download and need a model cache in Docker/AWS.
The retrieval eval in phase 6 compares dense against hybrid with the same interface.
