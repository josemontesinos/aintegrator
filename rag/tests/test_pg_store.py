"""Service-backed pgvector store test (run with `make service-test`)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from aintegrator_rag.corpus import chunk_documents, load_corpus
from aintegrator_rag.embed import HashingEmbedder, tokenize
from aintegrator_rag.models import EmbeddedChunk, RetrievalMode
from aintegrator_rag.retrieval import Retriever
from aintegrator_rag.store import PgVectorStore

pytestmark = pytest.mark.integration

_ROOT = Path(__file__).resolve().parents[2]
_DSN = os.getenv(
    "RAG_TEST_DATABASE_URL",
    "postgresql://aintegrator:aintegrator@localhost:5432/aintegrator",
)


async def test_pgvector_dense_lexical_and_hybrid_search() -> None:
    embedder = HashingEmbedder()
    chunks = chunk_documents(load_corpus(_ROOT / "rag" / "corpus"))
    vectors = embedder.embed_documents([chunk.text for chunk in chunks])
    entries = [
        EmbeddedChunk(chunk=chunk, vector=vector)
        for chunk, vector in zip(chunks, vectors, strict=True)
    ]
    store = await PgVectorStore.connect(_DSN, dimension=embedder.dimension)
    try:
        await store.initialize()
        await store.clear()
        await store.upsert(entries)
        assert await store.count() == len(entries)

        retriever = Retriever(embedder, store)
        for mode in RetrievalMode:
            result = await retriever.retrieve(
                "When is Fibernet planned maintenance?", mode=mode, top_k=3
            )
            assert result.hits, mode
            assert all(hit.chunk.chunk_id for hit in result.hits)

        filtered = await retriever.retrieve(
            "cancellation", mode=RetrievalMode.LEXICAL, provider="fibernet"
        )
        assert filtered.hits
        assert all(hit.chunk.provider == "fibernet" for hit in filtered.hits)
        assert all(hit.lexical_score > 0 for hit in filtered.hits)

        await store.clear()
        assert await store.count() == 0
    finally:
        await store.close()


def test_default_dsn_is_fictional_local_service() -> None:
    assert tokenize(_DSN)[:1] == ["postgresql"]
