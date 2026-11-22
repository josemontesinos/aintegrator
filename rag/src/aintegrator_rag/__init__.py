"""Hybrid retrieval over fictional provider documentation (phase 6)."""

from __future__ import annotations

from aintegrator_rag.corpus import chunk_documents, load_corpus, parse_document
from aintegrator_rag.embed import (
    Embedder,
    HashingEmbedder,
    SentenceTransformerEmbedder,
    build_embedder,
)
from aintegrator_rag.models import (
    Chunk,
    Citation,
    Document,
    EmbeddedChunk,
    GroundedAnswer,
    RetrievalHit,
    RetrievalMode,
    RetrievalResult,
)
from aintegrator_rag.retrieval import Retriever, build_grounded_answer
from aintegrator_rag.retrieval_eval import EvalQuery, EvalReport, ModeMetrics, run_eval
from aintegrator_rag.store import InMemoryStore, PgVectorStore, VectorStore

__all__ = [
    "Chunk",
    "Citation",
    "Document",
    "EmbeddedChunk",
    "Embedder",
    "EvalQuery",
    "EvalReport",
    "GroundedAnswer",
    "HashingEmbedder",
    "InMemoryStore",
    "ModeMetrics",
    "PgVectorStore",
    "RetrievalHit",
    "RetrievalMode",
    "RetrievalResult",
    "Retriever",
    "SentenceTransformerEmbedder",
    "VectorStore",
    "build_embedder",
    "build_grounded_answer",
    "chunk_documents",
    "load_corpus",
    "parse_document",
    "run_eval",
]

__version__ = "0.6.0"
