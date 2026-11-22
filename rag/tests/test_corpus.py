"""Tests for fictional corpus loading and deterministic chunking."""

from __future__ import annotations

from pathlib import Path

import pytest
from aintegrator_rag.corpus import chunk_documents, load_corpus, parse_document
from aintegrator_rag.models import Document

_ROOT = Path(__file__).resolve().parents[2]
_CORPUS = _ROOT / "rag" / "corpus"


def test_corpus_loads_four_fictional_documents() -> None:
    documents = load_corpus(_CORPUS)
    assert len(documents) == 4
    assert {document.doc_id for document in documents} == {
        "fibernet-guide",
        "legacytel-guide",
        "skylink-guide",
        "shared-policies",
    }
    assert all(document.source.startswith("fictional/") for document in documents)
    assert all(document.body for document in documents)


def test_chunks_are_unique_and_deterministic() -> None:
    documents = load_corpus(_CORPUS)
    first = chunk_documents(documents)
    second = chunk_documents(documents)
    assert len(first) >= 20
    assert {chunk.chunk_id for chunk in first} == {chunk.chunk_id for chunk in second}
    assert all(chunk.chunk_id.startswith(f"{chunk.doc_id}::") for chunk in first)
    assert all(len(chunk.text) <= 700 for chunk in first)
    assert all(chunk.provider and chunk.section and chunk.source for chunk in first)


def test_long_sections_are_split_with_overlap() -> None:
    document = Document(
        doc_id="synthetic",
        provider="fictional",
        title="Synthetic",
        version="1",
        source="fictional/synthetic.md",
        body="## Long section\n\n" + ("fictional words about eligibility and orders " * 40),
    )
    chunks = chunk_documents([document], max_chars=300, overlap=50)
    assert len(chunks) > 1
    assert [chunk.position for chunk in chunks] == list(range(len(chunks)))
    assert len({chunk.chunk_id for chunk in chunks}) == len(chunks)


def test_parse_document_rejects_missing_front_matter(tmp_path: Path) -> None:
    bad = tmp_path / "bad.md"
    bad.write_text("## No metadata\n\ntext", encoding="utf-8")
    with pytest.raises(ValueError, match="missing front matter"):
        parse_document(bad)


def test_invalid_chunk_settings_are_rejected() -> None:
    with pytest.raises(ValueError, match="max_chars"):
        chunk_documents([], max_chars=100)
