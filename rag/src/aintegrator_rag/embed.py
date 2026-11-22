"""Embedder interface with a deterministic offline default and a local BGE option."""

from __future__ import annotations

import hashlib
import itertools
import math
import os
import re
from collections import Counter
from typing import Protocol

__all__ = [
    "DEFAULT_DIMENSION",
    "DEFAULT_MODEL",
    "Embedder",
    "HashingEmbedder",
    "SentenceTransformerEmbedder",
    "build_embedder",
    "tokenize",
]

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
DEFAULT_DIMENSION = 384  # Also the dimension of the default BGE small model.
_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
_TOKEN = re.compile(r"[a-z0-9]+")


class Embedder(Protocol):
    """Convert text to unit-length vectors; the query may get a special prefix."""

    @property
    def name(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


def tokenize(text: str) -> list[str]:
    """Lower-case alphanumeric tokens used by both the hasher and BM25."""
    return _TOKEN.findall(text.lower())


class HashingEmbedder:
    """Dependency-free, reproducible embeddings for tests and CI.

    Signed feature hashing of unigrams, bigrams and sublinear term frequency gives
    stable, unit-length vectors without downloading a model. It is a retrieval
    baseline, not a semantic model; see ADR 010.
    """

    def __init__(self, dimension: int = DEFAULT_DIMENSION) -> None:
        if not 1 <= dimension <= 2000:
            raise ValueError("dimension must be between 1 and 2000")
        self._dimension = dimension

    @property
    def name(self) -> str:
        return f"hash-{self._dimension}"

    @property
    def dimension(self) -> int:
        return self._dimension

    def _embed(self, text: str) -> list[float]:
        tokens = tokenize(text)
        features: dict[str, float] = {}
        frequencies = Counter(tokens)
        for token, count in frequencies.items():
            features[token] = 1.0 + math.log(count)
        for first, second in itertools.pairwise(tokens):
            pair = f"{first}_{second}"
            features[pair] = features.get(pair, 0.0) + 1.0
        vector = [0.0] * self._dimension
        for feature, weight in features.items():
            digest = hashlib.sha256(feature.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self._dimension
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign * weight
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


class SentenceTransformerEmbedder:
    """Local `sentence-transformers` embedder; the heavy extra is optional."""

    def __init__(self, model: str = DEFAULT_MODEL) -> None:
        self._model_name = model
        self._model: object | None = None

    @property
    def name(self) -> str:
        return self._model_name

    @property
    def dimension(self) -> int:
        return DEFAULT_DIMENSION

    def _load(self) -> object:
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:  # pragma: no cover - depends on optional extra
                raise RuntimeError(
                    "RAG_EMBEDDER=local needs sentence-transformers; "
                    "install it with `uv sync --all-packages --extra local` "
                    "or use RAG_EMBEDDER=hash for offline runs"
                ) from exc
            self._model = SentenceTransformer(self._model_name)
        return self._model

    def _encode(self, texts: list[str]) -> list[list[float]]:
        model = self._load()
        vectors = model.encode(  # type: ignore[attr-defined]
            texts, normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False
        )
        return [[float(value) for value in row] for row in vectors]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._encode(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._encode([f"{_QUERY_PREFIX}{text}"])[0]


def build_embedder(
    name: str | None = None,
    *,
    model: str | None = None,
    dimension: int = DEFAULT_DIMENSION,
) -> Embedder:
    """Select an embedder by name, defaulting to the ADR 002 local model.

    ``RAG_EMBEDDER`` and ``RAG_MODEL`` provide defaults for CLI and library callers;
    an explicit argument always wins.
    """
    selected = name if name is not None else os.getenv("RAG_EMBEDDER", "local")
    selected_model = model if model is not None else os.getenv("RAG_MODEL") or DEFAULT_MODEL
    if selected in {"hash", "hashing", "offline"}:
        return HashingEmbedder(dimension)
    if selected in {"local", "bge", "sentence-transformers"}:
        return SentenceTransformerEmbedder(selected_model)
    raise ValueError(f"unknown embedder: {selected} (use 'local' or 'hash')")
