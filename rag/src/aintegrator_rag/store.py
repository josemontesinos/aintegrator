"""Vector stores: a deterministic in-memory store and a pgvector-backed store."""

from __future__ import annotations

import math
from collections import Counter
from typing import Any, Protocol

import asyncpg

from aintegrator_rag.embed import tokenize
from aintegrator_rag.fusion import reciprocal_rank_fusion
from aintegrator_rag.models import Chunk, EmbeddedChunk, RetrievalHit, RetrievalMode

__all__ = ["InMemoryStore", "PgVectorStore", "VectorStore"]

_BM25_K1 = 1.5
_BM25_B = 0.75


class VectorStore(Protocol):
    """Storage and ranking interface shared by every retrieval mode."""

    async def upsert(self, entries: list[EmbeddedChunk]) -> None: ...

    async def search(
        self,
        *,
        query: str,
        query_vector: list[float] | None,
        mode: RetrievalMode,
        top_k: int = 5,
        provider: str | None = None,
    ) -> list[RetrievalHit]: ...

    async def count(self) -> int: ...


def _cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        raise ValueError("vector dimension mismatch")
    return sum(a * b for a, b in zip(left, right, strict=False))


class InMemoryStore:
    """Pure-Python store used by unit tests, the CLI and the retrieval eval."""

    def __init__(self, entries: list[EmbeddedChunk] | None = None) -> None:
        self._entries = list(entries or [])

    async def upsert(self, entries: list[EmbeddedChunk]) -> None:
        by_id = {entry.chunk.chunk_id: entry for entry in self._entries}
        by_id.update({entry.chunk.chunk_id: entry for entry in entries})
        self._entries = [by_id[key] for key in sorted(by_id)]

    async def count(self) -> int:
        return len(self._entries)

    def to_snapshot(self) -> dict[str, Any]:
        """Serialize the whole index for an offline CLI snapshot."""
        return {
            "version": 1,
            "chunks": [entry.model_dump(mode="json") for entry in self._entries],
        }

    @classmethod
    def from_snapshot(cls, data: dict[str, Any]) -> InMemoryStore:
        if data.get("version") != 1:
            raise ValueError("unsupported index snapshot version")
        entries = [EmbeddedChunk.model_validate(item) for item in data.get("chunks", [])]
        return cls(entries)

    def _candidates(self, provider: str | None) -> list[EmbeddedChunk]:
        if provider is None:
            return list(self._entries)
        return [entry for entry in self._entries if entry.chunk.provider == provider]

    def _dense_ranking(
        self, candidates: list[EmbeddedChunk], query_vector: list[float]
    ) -> list[tuple[str, float]]:
        scored = [
            (entry.chunk.chunk_id, _cosine(query_vector, entry.vector)) for entry in candidates
        ]
        return sorted(scored, key=lambda item: (-item[1], item[0]))

    def _lexical_ranking(
        self, candidates: list[EmbeddedChunk], query: str
    ) -> list[tuple[str, float]]:
        documents = [
            # Headings are short, high-signal summaries; count them three times.
            tokenize(
                f"{entry.chunk.title} {entry.chunk.section} {entry.chunk.section} "
                f"{entry.chunk.section} {entry.chunk.text}"
            )
            for entry in candidates
        ]
        document_frequency: Counter[str] = Counter()
        for document in documents:
            document_frequency.update(set(document))
        average_length = sum(len(document) for document in documents) / max(len(documents), 1)
        query_tokens = tokenize(query)
        scored: list[tuple[str, float]] = []
        for entry, document in zip(candidates, documents, strict=False):
            frequencies = Counter(document)
            score = 0.0
            for term in query_tokens:
                count = frequencies.get(term, 0)
                if not count:
                    continue
                remaining = len(documents) - document_frequency[term] + 0.5
                idf = math.log(1 + remaining / (document_frequency[term] + 0.5))
                normalization = _BM25_K1 * (1 - _BM25_B + _BM25_B * len(document) / average_length)
                score += idf * (count * (_BM25_K1 + 1)) / (count + normalization)
            if score > 0:
                scored.append((entry.chunk.chunk_id, score))
        return sorted(scored, key=lambda item: (-item[1], item[0]))

    async def search(
        self,
        *,
        query: str,
        query_vector: list[float] | None,
        mode: RetrievalMode,
        top_k: int = 5,
        provider: str | None = None,
    ) -> list[RetrievalHit]:
        if top_k < 1:
            raise ValueError("top_k must be positive")
        if mode in {RetrievalMode.DENSE, RetrievalMode.HYBRID} and query_vector is None:
            raise ValueError(f"{mode} retrieval needs a query vector")
        candidates = self._candidates(provider)
        if not candidates:
            return []
        dense = (
            self._dense_ranking(candidates, query_vector)
            if mode in {RetrievalMode.DENSE, RetrievalMode.HYBRID} and query_vector is not None
            else []
        )
        lexical = (
            self._lexical_ranking(candidates, query)
            if mode in {RetrievalMode.LEXICAL, RetrievalMode.HYBRID}
            else []
        )
        by_id = {entry.chunk.chunk_id: entry for entry in candidates}
        dense_scores = dict(dense)
        lexical_scores = dict(lexical)
        if mode is RetrievalMode.HYBRID:
            candidate_limit = max(top_k * 4, 20)
            ranking = reciprocal_rank_fusion(
                [
                    [chunk_id for chunk_id, _ in dense[:candidate_limit]],
                    [chunk_id for chunk_id, _ in lexical[:candidate_limit]],
                ]
            )[:top_k]
            scores = ranking
        else:
            scores = (dense if mode is RetrievalMode.DENSE else lexical)[:top_k]
        return [
            RetrievalHit(
                chunk=by_id[chunk_id].chunk,
                score=score,
                dense_score=dense_scores.get(chunk_id, 0.0),
                lexical_score=lexical_scores.get(chunk_id, 0.0),
            )
            for chunk_id, score in scores
        ]


class PgVectorStore:
    """pgvector + PostgreSQL full-text search, using bound SQL parameters only."""

    def __init__(self, pool: Any, dimension: int) -> None:
        if not 1 <= dimension <= 2000:
            raise ValueError("dimension must be between 1 and 2000")
        self._pool = pool
        self._dimension = dimension

    @classmethod
    async def connect(cls, dsn: str, *, dimension: int) -> PgVectorStore:
        """Open a small connection pool for the given PostgreSQL DSN."""
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
        return cls(pool, dimension)

    async def close(self) -> None:
        await self._pool.close()

    async def initialize(self) -> None:
        """Create the extension, table and indexes (ingestion role only)."""
        async with self._pool.acquire() as connection:
            await connection.execute("CREATE EXTENSION IF NOT EXISTS vector")
            await connection.execute(
                f"""
                CREATE TABLE IF NOT EXISTS rag_chunks (
                    chunk_id TEXT PRIMARY KEY,
                    doc_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    title TEXT NOT NULL,
                    section TEXT NOT NULL,
                    source TEXT NOT NULL,
                    content TEXT NOT NULL,
                    embedding vector({self._dimension}) NOT NULL,
                    search_tsv TSVECTOR GENERATED ALWAYS AS (
                        to_tsvector('english', title || ' ' || section || ' ' || content)
                    ) STORED
                )
                """
            )
            await connection.execute(
                "CREATE INDEX IF NOT EXISTS rag_chunks_embedding_idx "
                "ON rag_chunks USING hnsw (embedding vector_cosine_ops)"
            )
            await connection.execute(
                "CREATE INDEX IF NOT EXISTS rag_chunks_tsv_idx ON rag_chunks USING gin (search_tsv)"
            )

    async def upsert(self, entries: list[EmbeddedChunk]) -> None:
        rows = [
            (
                entry.chunk.chunk_id,
                entry.chunk.doc_id,
                entry.chunk.provider,
                entry.chunk.title,
                entry.chunk.section,
                entry.chunk.source,
                entry.chunk.text,
                "[" + ",".join(f"{value:.8g}" for value in entry.vector) + "]",
            )
            for entry in entries
        ]
        if not rows:
            return
        async with self._pool.acquire() as connection:
            await connection.executemany(
                """
                INSERT INTO rag_chunks
                    (chunk_id, doc_id, provider, title, section, source, content, embedding)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8::vector)
                ON CONFLICT (chunk_id) DO UPDATE SET
                    doc_id = EXCLUDED.doc_id,
                    provider = EXCLUDED.provider,
                    title = EXCLUDED.title,
                    section = EXCLUDED.section,
                    source = EXCLUDED.source,
                    content = EXCLUDED.content,
                    embedding = EXCLUDED.embedding
                """,
                rows,
            )

    async def count(self) -> int:
        async with self._pool.acquire() as connection:
            return int(await connection.fetchval("SELECT count(*) FROM rag_chunks"))

    async def clear(self) -> None:
        """Remove all chunks; intended for ingestion and tests, not the MCP role."""
        async with self._pool.acquire() as connection:
            await connection.execute("DELETE FROM rag_chunks")

    async def _dense(
        self, connection: Any, query_vector: list[float], provider: str | None, limit: int
    ) -> list[dict[str, Any]]:
        literal = "[" + ",".join(f"{value:.8g}" for value in query_vector) + "]"
        rows = await connection.fetch(
            """
            SELECT chunk_id, doc_id, provider, title, section, source, content,
                   1 - (embedding <=> $1::vector) AS dense_score
            FROM rag_chunks
            WHERE ($2::text IS NULL OR provider = $2)
            ORDER BY embedding <=> $1::vector
            LIMIT $3
            """,
            literal,
            provider,
            limit,
        )
        return [dict(row) for row in rows]

    async def _lexical(
        self, connection: Any, query: str, provider: str | None, limit: int
    ) -> list[dict[str, Any]]:
        rows = await connection.fetch(
            """
            SELECT chunk_id, doc_id, provider, title, section, source, content,
                   ts_rank(search_tsv, plainto_tsquery('english', $1)) AS lexical_score
            FROM rag_chunks
            WHERE search_tsv @@ plainto_tsquery('english', $1)
              AND ($2::text IS NULL OR provider = $2)
            ORDER BY lexical_score DESC, chunk_id
            LIMIT $3
            """,
            query,
            provider,
            limit,
        )
        return [dict(row) for row in rows]

    @staticmethod
    def _hit(row: dict[str, Any], score: float, dense: float, lexical: float) -> RetrievalHit:
        return RetrievalHit(
            chunk=Chunk(
                chunk_id=row["chunk_id"],
                doc_id=row["doc_id"],
                provider=row["provider"],
                title=row["title"],
                section=row["section"],
                source=row["source"],
                text=row["content"],
                position=0,
            ),
            score=score,
            dense_score=dense,
            lexical_score=lexical,
        )

    async def search(
        self,
        *,
        query: str,
        query_vector: list[float] | None,
        mode: RetrievalMode,
        top_k: int = 5,
        provider: str | None = None,
    ) -> list[RetrievalHit]:
        if top_k < 1:
            raise ValueError("top_k must be positive")
        if mode in {RetrievalMode.DENSE, RetrievalMode.HYBRID} and query_vector is None:
            raise ValueError(f"{mode} retrieval needs a query vector")
        candidate_limit = max(top_k * 4, 20)
        async with self._pool.acquire() as connection:
            dense_rows = (
                await self._dense(connection, query_vector or [], provider, candidate_limit)
                if mode in {RetrievalMode.DENSE, RetrievalMode.HYBRID}
                else []
            )
            lexical_rows = (
                await self._lexical(connection, query, provider, candidate_limit)
                if mode in {RetrievalMode.LEXICAL, RetrievalMode.HYBRID}
                else []
            )
        if mode is RetrievalMode.DENSE:
            return [
                self._hit(row, float(row["dense_score"]), float(row["dense_score"]), 0.0)
                for row in dense_rows[:top_k]
            ]
        if mode is RetrievalMode.LEXICAL:
            return [
                self._hit(row, float(row["lexical_score"]), 0.0, float(row["lexical_score"]))
                for row in lexical_rows[:top_k]
            ]
        dense_scores = {row["chunk_id"]: float(row["dense_score"]) for row in dense_rows}
        lexical_scores = {row["chunk_id"]: float(row["lexical_score"]) for row in lexical_rows}
        rows_by_id = {row["chunk_id"]: row for row in [*dense_rows, *lexical_rows]}
        fused = reciprocal_rank_fusion(
            [
                [row["chunk_id"] for row in dense_rows],
                [row["chunk_id"] for row in lexical_rows],
            ]
        )[:top_k]
        return [
            self._hit(
                rows_by_id[chunk_id],
                score,
                dense_scores.get(chunk_id, 0.0),
                lexical_scores.get(chunk_id, 0.0),
            )
            for chunk_id, score in fused
        ]
