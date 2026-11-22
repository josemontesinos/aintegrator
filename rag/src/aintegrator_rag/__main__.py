"""CLI for ingestion, offline search, grounded answers and retrieval evals."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from aintegrator_rag.corpus import chunk_documents, load_corpus
from aintegrator_rag.embed import build_embedder
from aintegrator_rag.models import EmbeddedChunk, RetrievalMode
from aintegrator_rag.retrieval import Retriever
from aintegrator_rag.retrieval_eval import (
    compare_baseline,
    load_baseline,
    load_queries,
    run_eval,
    write_baseline,
)
from aintegrator_rag.store import InMemoryStore, PgVectorStore

_ROOT = Path(__file__).resolve().parents[3]
_CORPUS = _ROOT / "rag" / "corpus"
_INDEX = _ROOT / "rag" / "index.json"
_QUERIES = _ROOT / "rag" / "retrieval_eval.yaml"
_BASELINE = _ROOT / "rag" / "retrieval_baseline.json"
_MODES = tuple(mode.value for mode in RetrievalMode)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aintegrator-rag", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    ingest = commands.add_parser("ingest", help="chunk+embed the corpus into an index")
    ingest.add_argument("--corpus", type=Path, default=_CORPUS)
    ingest.add_argument("--output", type=Path, default=_INDEX)
    ingest.add_argument("--dsn", help="PostgreSQL DSN; omit to write an offline JSON snapshot")
    ingest.add_argument("--embedder", default="hash", choices=("hash", "local"))
    ingest.add_argument("--model")

    search = commands.add_parser("search", help="retrieve chunks from an offline index")
    search.add_argument("--index", type=Path, default=_INDEX)
    search.add_argument("--query", required=True)
    search.add_argument("--mode", default="hybrid", choices=_MODES)
    search.add_argument("--top-k", type=int, default=5)
    search.add_argument("--provider")
    search.add_argument("--embedder", default="hash", choices=("hash", "local"))
    search.add_argument("--model")

    answer = commands.add_parser("answer", help="produce a cited, extractive answer")
    answer.add_argument("--index", type=Path, default=_INDEX)
    answer.add_argument("--query", required=True)
    answer.add_argument("--mode", default="hybrid", choices=_MODES)
    answer.add_argument("--provider")
    answer.add_argument("--max-citations", type=int, default=3)
    answer.add_argument("--embedder", default="hash", choices=("hash", "local"))
    answer.add_argument("--model")

    evaluate = commands.add_parser("eval", help="compare dense, lexical and hybrid retrieval")
    evaluate.add_argument("--index", type=Path, default=_INDEX)
    evaluate.add_argument("--queries", type=Path, default=_QUERIES)
    evaluate.add_argument("--baseline", type=Path, default=_BASELINE)
    evaluate.add_argument("--update-baseline", action="store_true")
    evaluate.add_argument("--json", type=Path)
    evaluate.add_argument("--embedder", default="hash", choices=("hash", "local"))
    evaluate.add_argument("--model")
    return parser


def _retriever(args: argparse.Namespace) -> Retriever:
    embedder = build_embedder(args.embedder, model=args.model)
    data = json.loads(args.index.read_text(encoding="utf-8"))
    if data.get("version") != 1:
        raise ValueError("unsupported index snapshot version")
    return Retriever(embedder, InMemoryStore.from_snapshot(data))


async def _ingest(args: argparse.Namespace) -> int:
    embedder = build_embedder(args.embedder, model=args.model)
    documents = load_corpus(args.corpus)
    chunks = chunk_documents(documents)
    vectors = embedder.embed_documents([chunk.text for chunk in chunks])
    entries = [
        EmbeddedChunk(chunk=chunk, vector=vector)
        for chunk, vector in zip(chunks, vectors, strict=True)
    ]
    if args.dsn:
        pg_store = await PgVectorStore.connect(args.dsn, dimension=embedder.dimension)
        try:
            await pg_store.initialize()
            await pg_store.upsert(entries)
            count = await pg_store.count()
        finally:
            await pg_store.close()
    else:
        store = InMemoryStore()
        await store.upsert(entries)
        snapshot = store.to_snapshot()
        snapshot["embedder"] = embedder.name
        args.output.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(snapshot, indent=2, sort_keys=True) + "\n"
        args.output.write_text(payload, encoding="utf-8")
        count = len(entries)
    print(f"Indexed {count} chunks with {embedder.name}")
    return 0


async def _search(args: argparse.Namespace, *, grounded: bool) -> int:
    retriever = _retriever(args)
    mode = RetrievalMode(args.mode)
    if grounded:
        answer = await retriever.answer(
            args.query,
            mode=mode,
            top_k=max(args.max_citations, 5),
            provider=args.provider,
            max_citations=args.max_citations,
        )
        print(answer.markdown())
        return 0
    result = await retriever.retrieve(
        args.query, mode=mode, top_k=args.top_k, provider=args.provider
    )
    for rank, hit in enumerate(result.hits, start=1):
        print(
            f"{rank}. {hit.chunk_id} score={hit.score:.4f} "
            f"dense={hit.dense_score:.4f} lexical={hit.lexical_score:.4f}\n"
            f"   {hit.chunk.source} — {hit.chunk.section}\n"
            f"   {hit.chunk.text[:140]}"
        )
    return 0


async def _evaluate(args: argparse.Namespace) -> int:
    retriever = _retriever(args)
    queries = load_queries(args.queries)
    report = await run_eval(retriever, queries, top_k=5)
    for name, metrics in report.metrics.items():
        print(
            f"{name:8s} recall@5={metrics.recall_at_k:.3f} mrr={metrics.mrr:.3f} "
            f"ndcg@5={metrics.ndcg_at_k:.3f} hits={metrics.hits}/{report.query_count}"
        )
    if args.update_baseline:
        write_baseline(report, args.baseline)
        print(f"Wrote baseline to {args.baseline}")
        return 0
    ok, detail = compare_baseline(report, load_baseline(args.baseline))
    print(detail)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return 0 if ok else 1


async def _main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "ingest":
        return await _ingest(args)
    if args.command == "search":
        return await _search(args, grounded=False)
    if args.command == "answer":
        return await _search(args, grounded=True)
    return await _evaluate(args)


def main(argv: list[str] | None = None) -> None:
    """Console entry point; exits non-zero when the retrieval baseline drifts."""
    try:
        status = asyncio.run(_main(argv))
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        status = 2
    raise SystemExit(status)


if __name__ == "__main__":
    main()
