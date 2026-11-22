"""Tests for in-memory ranking, rank fusion and snapshot round-trips."""

from __future__ import annotations

from pathlib import Path

from aintegrator_rag.corpus import chunk_documents, load_corpus
from aintegrator_rag.embed import HashingEmbedder
from aintegrator_rag.fusion import reciprocal_rank_fusion
from aintegrator_rag.models import EmbeddedChunk, RetrievalMode
from aintegrator_rag.retrieval import Retriever
from aintegrator_rag.store import InMemoryStore

_ROOT = Path(__file__).resolve().parents[2]
_CORPUS = _ROOT / "rag" / "corpus"


async def _store() -> InMemoryStore:
    embedder = HashingEmbedder(64)
    chunks = chunk_documents(load_corpus(_CORPUS))
    vectors = embedder.embed_documents([chunk.text for chunk in chunks])
    store = InMemoryStore()
    await store.upsert(
        [
            EmbeddedChunk(chunk=chunk, vector=vector)
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]
    )
    return store


def test_rank_fusion_rewards_agreement_and_breaks_ties() -> None:
    fused = reciprocal_rank_fusion([["a", "b"], ["b", "a"]], k=10)
    assert [chunk_id for chunk_id, _ in fused] == ["a", "b"]
    assert fused[0][1] == fused[1][1]
    # Identical ranks tie-break on the chunk ID, so results stay deterministic.
    assert reciprocal_rank_fusion([["b", "a"], ["a", "b"]], k=10) == fused
    boosted = reciprocal_rank_fusion([["a", "b"], ["a", "b"]], k=10)
    assert boosted[0][1] > fused[0][1]


async def test_all_modes_return_ranked_hits() -> None:
    store = await _store()
    embedder = HashingEmbedder(64)
    for mode in RetrievalMode:
        result = await Retriever(embedder, store).retrieve(
            "When is Fibernet planned maintenance?", mode=mode, top_k=4
        )
        assert result.hits
        assert len(result.hits) <= 4
        assert len({hit.chunk_id for hit in result.hits}) == len(result.hits)
        assert all(hit.score > 0 for hit in result.hits)
        if mode is not RetrievalMode.LEXICAL:
            assert all(hit.dense_score > 0 for hit in result.hits)
        if mode is not RetrievalMode.DENSE:
            assert any(hit.lexical_score > 0 for hit in result.hits)


async def test_hybrid_combines_both_signals() -> None:
    store = await _store()
    result = await Retriever(HashingEmbedder(64), store).retrieve(
        "How do we prevent duplicate orders?", mode=RetrievalMode.HYBRID, top_k=5
    )
    assert any(hit.dense_score > 0 and hit.lexical_score > 0 for hit in result.hits)


async def test_provider_filter_and_snapshot_roundtrip() -> None:
    store = await _store()
    embedder = HashingEmbedder(64)
    result = await Retriever(embedder, store).retrieve(
        "Can I cancel a fibernet order?", mode=RetrievalMode.HYBRID, provider="fibernet"
    )
    assert result.hits
    assert all(hit.chunk.provider == "fibernet" for hit in result.hits)

    restored = InMemoryStore.from_snapshot(store.to_snapshot())
    assert await restored.count() == await store.count()
    again = await Retriever(embedder, restored).retrieve(
        "Can I cancel a fibernet order?", mode=RetrievalMode.HYBRID, provider="fibernet"
    )
    assert [hit.chunk_id for hit in again.hits] == [hit.chunk_id for hit in result.hits]


async def test_upsert_is_idempotent_and_input_is_validated() -> None:
    store = await _store()
    before = await store.count()
    await store.upsert([])
    assert await store.count() == before
    try:
        await store.search(query="x", query_vector=None, mode=RetrievalMode.DENSE, top_k=1)
    except ValueError as exc:
        assert "query vector" in str(exc)
    else:
        raise AssertionError("dense search without a vector must fail")
