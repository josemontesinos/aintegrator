"""Deterministic dense-vs-hybrid retrieval evaluation with a checked-in baseline."""

from __future__ import annotations

import json
import math
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from aintegrator_rag.models import RetrievalMode, RetrievalResult
from aintegrator_rag.retrieval import Retriever

__all__ = [
    "EvalQuery",
    "EvalReport",
    "ModeMetrics",
    "compare_baseline",
    "load_baseline",
    "load_queries",
    "run_eval",
    "write_baseline",
]

MODES = (RetrievalMode.DENSE, RetrievalMode.LEXICAL, RetrievalMode.HYBRID)


class EvalQuery(BaseModel):
    id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    # Chunk-ID prefixes (e.g. "fibernet-guide::cancellation"), not random UUIDs.
    relevant: list[str] = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)


class ModeMetrics(BaseModel):
    mode: RetrievalMode
    recall_at_k: float
    mrr: float
    ndcg_at_k: float
    hits: int


class QueryScore(BaseModel):
    query_id: str
    mode: RetrievalMode
    relevant_found: bool
    reciprocal_rank: float
    retrieved: list[str]


class EvalReport(BaseModel):
    top_k: int
    query_count: int
    metrics: dict[str, ModeMetrics]
    scores: list[QueryScore]


def load_queries(path: Path) -> list[EvalQuery]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    queries = data.get("queries", []) if isinstance(data, dict) else []
    if not queries:
        raise ValueError(f"no eval queries in {path}")
    return [EvalQuery.model_validate(item) for item in queries]


def _is_relevant(chunk_id: str, prefixes: list[str]) -> bool:
    return any(chunk_id.startswith(prefix) for prefix in prefixes)


def _score(query: EvalQuery, result: RetrievalResult, top_k: int) -> QueryScore:
    ranks = [
        index
        for index, hit in enumerate(result.hits[:top_k], start=1)
        if _is_relevant(hit.chunk_id, query.relevant)
    ]
    reciprocal = 1.0 / ranks[0] if ranks else 0.0
    return QueryScore(
        query_id=query.id,
        mode=result.mode,
        relevant_found=bool(ranks),
        reciprocal_rank=reciprocal,
        retrieved=[hit.chunk_id for hit in result.hits[:top_k]],
    )


async def run_eval(retriever: Retriever, queries: list[EvalQuery], *, top_k: int = 5) -> EvalReport:
    """Run every query in every mode and compute recall@k, MRR and nDCG@k."""
    scores: list[QueryScore] = []
    ndcg_by_mode: dict[RetrievalMode, list[float]] = {mode: [] for mode in MODES}
    for query in queries:
        for mode in MODES:
            result = await retriever.retrieve(query.query, mode=mode, top_k=top_k)
            score = _score(query, result, top_k)
            scores.append(score)
            ranks = [
                index
                for index, chunk_id in enumerate(score.retrieved, start=1)
                if _is_relevant(chunk_id, query.relevant)
            ]
            dcg = sum(1.0 / math.log2(rank + 1) for rank in ranks)
            ideal = sum(
                1.0 / math.log2(rank + 1) for rank in range(1, min(len(query.relevant), top_k) + 1)
            )
            ndcg_by_mode[mode].append(dcg / ideal if ideal else 0.0)
    metrics: dict[str, ModeMetrics] = {}
    for mode in MODES:
        mode_scores = [score for score in scores if score.mode is mode]
        count = len(mode_scores)
        metrics[mode.value] = ModeMetrics(
            mode=mode,
            recall_at_k=round(sum(score.relevant_found for score in mode_scores) / count, 6),
            mrr=round(sum(score.reciprocal_rank for score in mode_scores) / count, 6),
            ndcg_at_k=round(sum(ndcg_by_mode[mode]) / count, 6),
            hits=sum(score.relevant_found for score in mode_scores),
        )
    return EvalReport(top_k=top_k, query_count=len(queries), metrics=metrics, scores=scores)


def write_baseline(report: EvalReport, path: Path) -> None:
    payload = {
        "version": 1,
        "top_k": report.top_k,
        "metrics": {
            name: {
                "recall_at_k": metrics.recall_at_k,
                "mrr": metrics.mrr,
                "ndcg_at_k": metrics.ndcg_at_k,
            }
            for name, metrics in report.metrics.items()
        },
        "note": "Generated from the deterministic hash embedder; corpus changes require a refresh.",
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_baseline(path: Path) -> dict[str, object]:
    data: dict[str, object] = json.loads(path.read_text(encoding="utf-8"))
    if data.get("version") != 1:
        raise ValueError("unsupported retrieval baseline version")
    return data


def compare_baseline(report: EvalReport, baseline: dict[str, object]) -> tuple[bool, str]:
    """Require every stable metric to match; hybrid must also beat dense on recall."""
    expected = baseline.get("metrics", {})
    if not isinstance(expected, dict):
        return False, "baseline has no metrics"
    details: list[str] = []
    for name, metrics in report.metrics.items():
        recorded = expected.get(name, {})
        for field in ("recall_at_k", "mrr", "ndcg_at_k"):
            actual = getattr(metrics, field)
            previous = recorded.get(field) if isinstance(recorded, dict) else None
            if previous is None or abs(float(previous) - actual) > 1e-6:
                details.append(f"{name}.{field}: {previous} -> {actual}")
    hybrid = report.metrics[RetrievalMode.HYBRID.value].recall_at_k
    dense = report.metrics[RetrievalMode.DENSE.value].recall_at_k
    if hybrid < dense:
        details.append(f"hybrid recall {hybrid} below dense recall {dense}")
    if details:
        return False, ", ".join(details)
    return True, "retrieval metrics match baseline; hybrid recall >= dense recall"
