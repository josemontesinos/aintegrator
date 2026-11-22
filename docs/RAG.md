# RAG over fictional provider documentation

**Introduced in phase 6; current release `v1.0`.** The `aintegrator-rag` workspace package ingests four
fictional provider documents, embeds deterministic chunks, retrieves with dense, lexical or hybrid
search behind one interface, and produces extractive answers with citations. The default offline
path needs no model download, API key or Docker stack; the local `bge-small-en-v1.5` model is an
optional extra (ADR 002).

## Layout

```
rag/corpus/*.md              # four fictional documents with YAML front matter
rag/retrieval_eval.yaml      # 14 queries with stable relevant-chunk prefixes
rag/retrieval_baseline.json  # checked-in retrieval metrics gate
rag/index.json               # generated offline index (gitignored)
rag/src/aintegrator_rag/     # corpus, embed, fusion, store, retrieval, eval, CLI
rag/tests/                   # unit tests + one pgvector service test
```

## Quickstart

```bash
make rag-index             # chunk + embed the corpus into rag/index.json
make rag-eval              # rebuild the index, then dense vs lexical vs hybrid gate
uv run python -m aintegrator_rag search --query "Can I cancel a fibernet order?"
uv run python -m aintegrator_rag answer --query "When is Fibernet maintenance?"
make rag-ingest-postgres   # optional: load pgvector in the local compose stack (RAG_DSN override)
```

For the local semantic model, first install the optional extra and then select it explicitly:

```bash
uv sync --all-packages --extra local
RAG_EMBEDDER=local uv run python -m aintegrator_rag ingest --embedder local
```

`build_embedder()` honours `RAG_EMBEDDER` (`local` by default per ADR 002, `hash` for reproducible
CI) and `RAG_MODEL`; explicit arguments and CLI flags win over both. The CLI defaults to
`--embedder hash` so every checked-in result is reproducible. The `SentenceTransformerEmbedder`
prefixes queries as recommended for BGE v1.5 and normalizes all vectors; a model override must also
produce 384-dimensional vectors (the pgvector column and offline hash dimension are fixed at 384).
If the extra is missing it fails with a clear install hint rather than silently changing retrieval
quality.

## Corpus and chunking

Each markdown file starts with front matter: `doc_id`, `provider`, `title`, `version` and a
`fictional/...` source path. `##` headings become sections; sections longer than 700 characters
are split into overlapping word-boundary windows (100-character overlap). A chunk ID is
`{doc_id}::{heading-slug}::{position}`, for example `fibernet-guide::cancellation::03`.

Stable IDs are the interface between the corpus and the retrieval eval: rephrasing a paragraph does
not invalidate a relevance judgement, but renaming a heading does (and requires a baseline refresh).

## One interface, three modes

| Mode | Signal | Implementation |
| --- | --- | --- |
| `dense` | cosine similarity of query/document vectors | hashed or BGE embeddings, unit length |
| `lexical` | BM25 over title, heading (3×) and text | pure Python, deterministic tie-break on chunk ID |
| `hybrid` | reciprocal rank fusion of both rankings | `RRF_K = 10`, tuned for a ~21-chunk corpus |

`Retriever` embeds the query only for dense/hybrid, then calls the same `VectorStore.search` method
for every mode. `InMemoryStore` backs unit tests, the CLI and evals; `PgVectorStore` uses pgvector
(`embedding vector(384)`, cosine distance via `<=>`) plus a generated `tsvector` column with
`plainto_tsquery` and `ts_rank`. Both stores fuse rankings with the *same* `reciprocal_rank_fusion`
helper, so dense-vs-hybrid comparisons are meaningful. The classic `k=60` was reduced to 10 because
on a small corpus it flattens rank differences and rewards any chunk present in both lists over a
chunk ranked first by one of them.

All pgvector SQL uses bound parameters; the only interpolation is the embedder dimension in DDL,
validated as an integer. `initialize()` creates the extension, table and indexes and must be run
with the ingestion role; retrieval needs only `SELECT`, consistent with ADR 008.

## Grounded answers and untrusted text

`Retriever.answer()` and `build_grounded_answer()` are extractive: each selected chunk contributes
one short quoted snippet followed by `[n]`, and `GroundedAnswer.markdown()` appends a numbered
source list with document, section, provider and chunk ID. There is no LLM in this path, so an
answer cannot invent a citation. If nothing is retrieved the answer says so explicitly.

Provider documents and provider responses are **data**. Instruction-like text found in a chunk is
quoted as evidence and never acted upon; the RAG package exposes no write tool and imports no
agent, MCP, integration or provider package (enforced by import-linter). Answers never include
secrets, approval tokens or credentials.

## Retrieval eval

`rag/retrieval_eval.yaml` holds 14 queries tagged by concern (cancellation, pricing, recovery,
security, eligibility, operations). Relevance is expressed as chunk-ID prefixes. For each mode the
runner computes recall@5, MRR and nDCG@5 and compares all nine numbers to
`rag/retrieval_baseline.json`; latency is not part of this eval. `make rag-eval` rebuilds the index
first and exits non-zero on drift or if hybrid recall falls below dense recall. Refresh the baseline
deliberately with `make rag-eval-baseline` after an intentional corpus or ranking change.

Current baseline (hash-384 embedder, top_k=5):

| Mode | recall@5 | MRR | nDCG@5 |
| --- | --- | --- | --- |
| dense | 0.714 | 0.613 | 0.612 |
| lexical | 1.000 | 0.860 | 0.894 |
| hybrid | 0.929 | 0.798 | 0.821 |

Lexical is strongest here because the questions were written from the same fictional vocabulary;
hybrid still recovers 3 queries that dense alone misses, which is the comparison ADR 002 calls
for. The local BGE model is intentionally not used for the baseline: model revisions would make CI
non-reproducible.

## Service-backed test

`rag/tests/test_pg_store.py` is marked `integration` and runs with `make service-test` once
`make up` is healthy. It creates the schema, ingests the corpus, exercises all three modes against
real pgvector, checks the provider filter and clears the table. Override the DSN with
`RAG_TEST_DATABASE_URL` if needed.
