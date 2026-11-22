"""Typed records for documents, chunks, retrieval hits and grounded answers."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

__all__ = [
    "Chunk",
    "Citation",
    "Document",
    "EmbeddedChunk",
    "GroundedAnswer",
    "RetrievalHit",
    "RetrievalMode",
    "RetrievalResult",
]


class RetrievalMode(StrEnum):
    """The three retrieval strategies exposed with one interface."""

    DENSE = "dense"
    LEXICAL = "lexical"
    HYBRID = "hybrid"


class Document(BaseModel):
    """A source markdown file from the fictional corpus."""

    doc_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    title: str = Field(min_length=1)
    version: str = Field(min_length=1)
    source: str = Field(min_length=1)
    body: str = Field(min_length=1)


class Chunk(BaseModel):
    """A deterministic slice of a document, small enough to cite on its own."""

    chunk_id: str = Field(min_length=1)
    doc_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    title: str = Field(min_length=1)
    section: str = Field(min_length=1)
    source: str = Field(min_length=1)
    text: str = Field(min_length=1)
    position: int = Field(ge=0)


class EmbeddedChunk(BaseModel):
    """A chunk paired with its embedding vector."""

    chunk: Chunk
    vector: list[float] = Field(min_length=1)


class RetrievalHit(BaseModel):
    """A retrieved chunk with the scores that placed it in the ranking."""

    chunk: Chunk
    score: float
    dense_score: float = 0.0
    lexical_score: float = 0.0

    @property
    def chunk_id(self) -> str:
        return self.chunk.chunk_id


class RetrievalResult(BaseModel):
    """Ordered hits for one query and one retrieval mode."""

    query: str
    mode: RetrievalMode
    hits: list[RetrievalHit]


class Citation(BaseModel):
    """A verifiable pointer back to a retrieved chunk."""

    number: int = Field(ge=1)
    chunk_id: str
    provider: str
    source: str
    section: str
    quote: str


class GroundedAnswer(BaseModel):
    """An extractive answer: every claim is paired with a citation."""

    query: str
    answer: str
    citations: list[Citation]

    def markdown(self) -> str:
        """Render the answer followed by a numbered source list."""
        if not self.citations:
            return self.answer
        sources = "\n".join(
            f"[{citation.number}] {citation.source} — {citation.section} "
            f"({citation.provider}) [{citation.chunk_id}]"
            for citation in self.citations
        )
        return f"{self.answer}\n\nSources:\n{sources}"
