"""Tests for the retrieval eval and its checked-in baseline gate."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from aintegrator_rag.corpus import chunk_documents, load_corpus
from aintegrator_rag.embed import HashingEmbedder
from aintegrator_rag.models import EmbeddedChunk
from aintegrator_rag.retrieval import Retriever
from aintegrator_rag.retrieval_eval import (
    EvalReport,
    compare_baseline,
    load_baseline,
    load_queries,
    run_eval,
    write_baseline,
)
from aintegrator_rag.store import InMemoryStore

_ROOT = Path(__file__).resolve().parents[2]
_QUERIES = _ROOT / "rag" / "retrieval_eval.yaml"
_BASELINE = _ROOT / "rag" / "retrieval_baseline.json"


def test_queries_are_unique_and_well_formed() -> None:
    queries = load_queries(_QUERIES)
    assert len(queries) >= 12
    assert len({query.id for query in queries}) == len(queries)
    assert all(query.relevant and query.query for query in queries)


async def _report() -> EvalReport:
    embedder = HashingEmbedder()
    chunks = chunk_documents(load_corpus(_ROOT / "rag" / "corpus"))
    vectors = embedder.embed_documents([chunk.text for chunk in chunks])
    store = InMemoryStore()
    await store.upsert(
        [
            EmbeddedChunk(chunk=chunk, vector=vector)
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]
    )
    return await run_eval(Retriever(embedder, store), load_queries(_QUERIES), top_k=5)


async def test_eval_report_and_baseline_gate() -> None:
    report = await _report()
    assert report.query_count >= 12
    assert set(report.metrics) == {"dense", "lexical", "hybrid"}
    hybrid = report.metrics["hybrid"]
    dense = report.metrics["dense"]
    assert hybrid.recall_at_k > dense.recall_at_k
    ok, detail = compare_baseline(report, load_baseline(_BASELINE))
    assert ok, detail

    drifted = deepcopy(load_baseline(_BASELINE))
    drifted["metrics"]["hybrid"]["recall_at_k"] = 0.0  # type: ignore[index]
    ok, detail = compare_baseline(report, drifted)
    assert not ok
    assert "hybrid.recall_at_k" in detail


async def test_baseline_roundtrip(tmp_path: Path) -> None:
    report = await _report()
    path = tmp_path / "baseline.json"
    write_baseline(report, path)
    baseline = load_baseline(path)
    assert json.loads(path.read_text(encoding="utf-8")) == baseline
    ok, detail = compare_baseline(report, baseline)
    assert ok, detail
