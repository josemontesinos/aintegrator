"""Load the fictional documentation corpus and split it into stable chunks."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from aintegrator_rag.models import Chunk, Document

__all__ = ["chunk_documents", "load_corpus", "parse_document"]

_HEADING = re.compile(r"^##\s+(.+?)\s*$")
_REQUIRED = ("doc_id", "provider", "title", "version", "source")


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "section"


def parse_document(path: Path) -> Document:
    """Parse one markdown file with a small YAML front matter header."""
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"{path}: missing front matter")
    end = next(
        (index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"),
        None,
    )
    if end is None:
        raise ValueError(f"{path}: unterminated front matter")
    meta = yaml.safe_load("\n".join(lines[1:end])) or {}
    if not isinstance(meta, dict):
        raise ValueError(f"{path}: front matter must be a mapping")
    missing = [key for key in _REQUIRED if not meta.get(key)]
    if missing:
        raise ValueError(f"{path}: missing front matter keys: {', '.join(missing)}")
    body = "\n".join(lines[end + 1 :]).strip()
    if not body:
        raise ValueError(f"{path}: empty document")
    return Document(body=body, **{key: str(meta[key]) for key in _REQUIRED})


def load_corpus(root: Path) -> list[Document]:
    """Load every markdown file in a corpus directory, sorted by file name."""
    if not root.is_dir():
        raise ValueError(f"corpus directory does not exist: {root}")
    return [parse_document(path) for path in sorted(root.glob("*.md"))]


def _sections(document: Document) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    heading = "overview"
    buffer: list[str] = []
    for line in document.body.splitlines():
        match = _HEADING.match(line)
        if match:
            if buffer:
                sections.append((heading, "\n".join(buffer).strip()))
            heading = match.group(1)
            buffer = []
        else:
            buffer.append(line)
    if buffer:
        sections.append((heading, "\n".join(buffer).strip()))
    return [(name, text) for name, text in sections if text]


def _windows(text: str, max_chars: int, overlap: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    windows: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            boundary = text.rfind(" ", start + max_chars - overlap, end)
            if boundary > start:
                end = boundary
        window = text[start:end].strip()
        if window:
            windows.append(window)
        if end >= len(text):
            break
        start = max(start + 1, end - overlap)
    return windows


def chunk_documents(
    documents: list[Document], *, max_chars: int = 700, overlap: int = 100
) -> list[Chunk]:
    """Split documents into deterministic, addressable chunks.

    Chunk IDs combine the document ID, heading slug and position, so the retrieval
    eval can refer to a section without depending on exact wording or random IDs.
    """
    if max_chars < 200 or not 0 <= overlap < max_chars:
        raise ValueError("require max_chars >= 200 and 0 <= overlap < max_chars")
    chunks: list[Chunk] = []
    for document in documents:
        position = 0
        for section, text in _sections(document):
            for window in _windows(text, max_chars, overlap):
                chunk_id = f"{document.doc_id}::{_slug(section)}::{position:02d}"
                chunks.append(
                    Chunk(
                        chunk_id=chunk_id,
                        doc_id=document.doc_id,
                        provider=document.provider,
                        title=document.title,
                        section=section,
                        source=document.source,
                        text=window,
                        position=position,
                    )
                )
                position += 1
    return chunks
