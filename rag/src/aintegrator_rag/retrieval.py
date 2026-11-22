"""One retrieval interface for dense, lexical and hybrid modes, plus grounded answers."""

from __future__ import annotations

import re

from aintegrator_rag.embed import Embedder
from aintegrator_rag.models import (
    Citation,
    GroundedAnswer,
    RetrievalHit,
    RetrievalMode,
    RetrievalResult,
)
from aintegrator_rag.store import VectorStore

__all__ = ["Retriever", "build_grounded_answer"]

_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_NO_HITS = "No relevant documentation was found for that question."


def _snippet(text: str, max_chars: int = 260) -> str:
    sentences = _SENTENCE.split(text.replace("\n", " "))
    chosen: list[str] = []
    length = 0
    for sentence in sentences:
        candidate = sentence.strip()
        if not candidate:
            continue
        if chosen and length + len(candidate) + 1 > max_chars:
            break
        chosen.append(candidate)
        length += len(candidate) + 1
    snippet = " ".join(chosen)
    if len(snippet) > max_chars:
        snippet = snippet[:max_chars].rsplit(" ", 1)[0].rstrip(" ,;:.") + "…"
    return snippet


def build_grounded_answer(
    query: str, result: RetrievalResult, *, max_citations: int = 3
) -> GroundedAnswer:
    """Build an extractive answer where every statement carries a citation.

    Retrieved text is always treated as data to quote, never as an instruction to
    follow (see ADR 010). No LLM is involved in this path.
    """
    if max_citations < 1:
        raise ValueError("max_citations must be positive")
    hits: list[RetrievalHit] = result.hits[:max_citations]
    if not hits:
        return GroundedAnswer(query=query, answer=_NO_HITS, citations=[])
    citations = [
        Citation(
            number=number,
            chunk_id=hit.chunk.chunk_id,
            provider=hit.chunk.provider,
            source=hit.chunk.source,
            section=hit.chunk.section,
            quote=_snippet(hit.chunk.text),
        )
        for number, hit in enumerate(hits, start=1)
    ]
    answer = " ".join(f"{citation.quote} [{citation.number}]" for citation in citations)
    return GroundedAnswer(query=query, answer=answer, citations=citations)


class Retriever:
    """Retrieve chunks and optionally produce a cited, extractive answer."""

    def __init__(self, embedder: Embedder, store: VectorStore) -> None:
        self._embedder = embedder
        self._store = store

    async def retrieve(
        self,
        query: str,
        *,
        mode: RetrievalMode = RetrievalMode.HYBRID,
        top_k: int = 5,
        provider: str | None = None,
    ) -> RetrievalResult:
        if not query.strip():
            raise ValueError("query must not be empty")
        query_vector = (
            self._embedder.embed_query(query)
            if mode in {RetrievalMode.DENSE, RetrievalMode.HYBRID}
            else None
        )
        hits = await self._store.search(
            query=query,
            query_vector=query_vector,
            mode=mode,
            top_k=top_k,
            provider=provider,
        )
        return RetrievalResult(query=query, mode=mode, hits=hits)

    async def answer(
        self,
        query: str,
        *,
        mode: RetrievalMode = RetrievalMode.HYBRID,
        top_k: int = 5,
        provider: str | None = None,
        max_citations: int = 3,
    ) -> GroundedAnswer:
        result = await self.retrieve(query, mode=mode, top_k=top_k, provider=provider)
        return build_grounded_answer(query, result, max_citations=max_citations)
