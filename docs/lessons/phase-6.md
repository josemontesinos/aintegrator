# Phase 6 lessons — RAG

- **A small corpus changes rank fusion.** With ~21 chunks, the textbook `k=60` in reciprocal rank
  fusion flattens rank differences: any chunk appearing in both lists beat a chunk ranked first by
  one list. Reducing `k` to 10 immediately lifted hybrid recall from 0.857 to 0.929. Constants from
  large-corpus papers need checking against the actual index size.
- **Heading boosting is cheap and effective.** Counting section headings three times in the BM25
  document improved lexical MRR from 0.824 to 0.860 without changing hybrid recall. Short fields
  carry disproportionate intent.
- **Keep the embedder out of the baseline by default.** A local model revision would move every
  metric. The hash embedder is deterministic, dependency-free and dimension-compatible with BGE;
  the optional `--embedder local` path can be benchmarked manually.
- **Stable chunk IDs are an API.** They let 14 relevance judgements survive paragraph rewrites.
  Renaming a heading breaks them, which is a useful signal that the taxonomy changed.
- **Extraction makes citations structural.** Quoting snippets and rendering `[n]` markers means an
  answer cannot cite a chunk it did not retrieve. No judge is needed to catch invented sources.
- **The two stores must share ranking code.** Calling the same `reciprocal_rank_fusion` helper from
  the in-memory and pgvector stores prevented a subtle divergence: the first bug was passing two
  positional lists to a function that expected one list of lists, which only surfaced in the CLI.
- **Mark generated indexes as generated.** `rag/index.json` is gitignored and rebuilt by
  `make rag-index`; otherwise a stale index would silently invalidate the retrieval baseline.
- **Service-backed retrieval is worth one extra test.** The pgvector integration test covers what
  the unit suite cannot see: generated `tsvector` columns, the `vector` cast in bound parameters
  and the read-only provider filter all have to work against real PostgreSQL 16.
