# 010 — Hybrid retrieval with rank fusion and extractive citations

- Status: accepted
- Date: 2026-11-22
- Phase: 6

## Context

Phase 5 proved the agent's decision loop with cassettes, but the agent still has no way to cite
factual provider documentation. A piece of documentation may be found by meaning ("how do I undo
an order?") or by exact vocabulary ("legacytel:LEG-102", "UNSUPPORTED_OPERATION"). A dense-only
retriever misses codes and product names; a lexical-only retriever misses paraphrases. ADR 002
requires comparing dense and hybrid retrieval through one interface and defaulting to a local
embedding model.

We also need answers whose claims can be verified. Letting an LLM paraphrase retrieved chunks
reintroduces the nondeterminism that phase 5 deliberately removed from CI, and a fluent model can
attribute a statement to the wrong source.

## Decision

The RAG package exposes one `VectorStore.search` interface with three modes: dense cosine
similarity, BM25 lexical scoring and hybrid reciprocal rank fusion of both rankings. The same
fusion helper is used by the in-memory store and the pgvector store, so the dense-vs-hybrid eval
measures ranking, not store-specific behaviour. For the small fictional corpus, RRF uses `k=10`
rather than the classic 60; larger corpora can revisit this constant.

Corpus chunks have stable IDs (`doc_id::heading-slug::position`) and the eval judges relevance by
prefix. Fourteen queries run in all three modes against a hash-based offline embedder; recall@5,
MRR and nDCG@5 are compared with a checked-in baseline. `local` selects the ADR 002
`BAAI/bge-small-en-v1.5` model behind an optional extra; CI uses `hash` so results do not change
when a model revision ships.

Answers are **extractive**: each claim is a quoted snippet followed by `[n]`, with a numbered
source list naming document, section, provider and chunk ID. No LLM is in the answer path.
Retrieved text, including any instruction-like words, is always data to quote and never a command
to execute. The RAG package depends on no other workspace layer.

## Alternatives considered

- **Dense-only retrieval with pgvector** — rejected: misses exact offer IDs and error codes.
- **Lexical-only full-text search** — rejected: misses paraphrases and violates ADR 002.
- **Weighted RRF tuned to make hybrid always beat lexical** — rejected: would fit the eval rather
  than describe retrieval quality; the report shows lexical stronger on this small, lexically
  aligned corpus while hybrid clearly beats dense.
- **LLM-generated answers with model-chosen citations** — rejected: non-reproducible and can
  fabricate attributions; can be layered on top later if a live-model smoke test is needed.
- **Random UUIDs for chunks** — rejected: they would make relevance judgements and baselines
  meaningless.

## Consequences

The retrieval eval is as reproducible as the phase 5 agent eval and runs without Docker, keys or
model downloads. Citation correctness is structural rather than judged: if a claim appears, the
corresponding chunk ID is in the answer. The trade-off is terser, less fluent prose; the RAG API
can still hand its cited snippets to the agent's LLM as grounded context. Changing corpus wording
is safe, but renaming a heading changes chunk IDs and requires `make rag-eval-baseline`.
