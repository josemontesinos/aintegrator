"""Tests for the embedder interface and its offline default."""

from __future__ import annotations

import math
import sys

import pytest
from aintegrator_rag.embed import (
    HashingEmbedder,
    SentenceTransformerEmbedder,
    build_embedder,
    tokenize,
)


def test_hash_embedder_is_reproducible_and_normalized() -> None:
    embedder = HashingEmbedder(64)
    first = embedder.embed_query("cancel a fibernet order")
    assert first == embedder.embed_query("cancel a fibernet order")
    assert len(first) == 64
    assert abs(math.sqrt(sum(value * value for value in first)) - 1.0) < 1e-9
    assert len(embedder.embed_documents(["one", "two"])) == 2


def test_hash_embedder_validates_dimension() -> None:
    with pytest.raises(ValueError, match="dimension"):
        HashingEmbedder(0)


def test_tokenize_is_lowercase_alphanumeric() -> None:
    assert tokenize("Fibre-Plus costs 49.00 EUR!") == [
        "fibre",
        "plus",
        "costs",
        "49",
        "00",
        "eur",
    ]


def test_build_embedder_respects_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_EMBEDDER", "hash")
    assert isinstance(build_embedder(), HashingEmbedder)
    monkeypatch.setenv("RAG_EMBEDDER", "local")
    monkeypatch.setenv("RAG_MODEL", "fictional/embedder")
    embedder = build_embedder()
    assert isinstance(embedder, SentenceTransformerEmbedder)
    assert embedder.name == "fictional/embedder"
    assert build_embedder("hash").name.startswith("hash-")
    with pytest.raises(ValueError, match="unknown embedder"):
        build_embedder("hosted")


def test_local_embedder_reports_missing_optional_extra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    embedder = SentenceTransformerEmbedder()
    with pytest.raises(RuntimeError, match="uv sync --all-packages --extra local"):
        embedder.embed_documents(["fictional document"])
