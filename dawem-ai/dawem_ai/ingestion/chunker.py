"""Text chunker with page-exact metadata.

Chunks never span pages, so every chunk's ``page_number`` is correct. Splitting prefers
paragraph breaks, then sentence ends, then whitespace, and only hard-cuts a token longer
than ``chunk_size``. Consecutive chunks on a page overlap by ~``overlap`` characters so a
sentence cut at a boundary is still whole in one of the two chunks.

``chunk_id`` is a deterministic hash of (user_id, file_id, sequence number): re-indexing
the same file reproduces the same ids (idempotent upserts), and ids stay short and
unique even when two users happen to upload files with the same ``file_id``.

Defaults (500/80 chars) fit the 128-token limit of the default multilingual embedder;
see ``.env.example``.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Sequence

from dawem_ai.ingestion.loaders import PageText

__all__ = ["Chunk", "chunk_pages", "make_chunk_id"]

# Sentence ends for Latin and Arabic punctuation (., !, ?, ؟, ۔) and newlines.
_SENTENCE_END_RE = re.compile(r"(?<=[.!?؟۔])\s+|\n+")


@dataclass(frozen=True, slots=True)
class Chunk:
    chunk_id: str
    text: str
    page_number: int | None
    chunk_index: int  # 0-based position within the file


def make_chunk_id(user_id: str, file_id: str, index: int) -> str:
    digest = hashlib.sha256(f"{user_id}\x00{file_id}\x00{index}".encode()).hexdigest()
    return digest[:32]


def _hard_split(text: str, size: int) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)]


def _units(text: str, size: int) -> list[str]:
    """Break text into pieces that are each <= size, preferring sentence boundaries."""
    pieces: list[str] = []
    for sentence in _SENTENCE_END_RE.split(text):
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(sentence) <= size:
            pieces.append(sentence)
            continue
        # Over-long sentence: pack words greedily, hard-cut words longer than size.
        current = ""
        for word in sentence.split():
            if len(word) > size:
                if current:
                    pieces.append(current)
                    current = ""
                pieces.extend(_hard_split(word, size))
            elif not current:
                current = word
            elif len(current) + 1 + len(word) <= size:
                current += " " + word
            else:
                pieces.append(current)
                current = word
        if current:
            pieces.append(current)
    return pieces


def _split_page(text: str, size: int, overlap: int) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    length = 0
    for unit in _units(text, size):
        added = len(unit) + (1 if current else 0)
        if current and length + added > size:
            chunks.append(" ".join(current))
            # Seed the next chunk with trailing units totalling <= overlap characters.
            seed: list[str] = []
            seed_len = 0
            for prev in reversed(current):
                extra = len(prev) + (1 if seed else 0)
                if seed_len + extra > overlap or seed_len + extra + len(unit) + 1 > size:
                    break
                seed.insert(0, prev)
                seed_len += extra
            current, length = seed, seed_len
            added = len(unit) + (1 if current else 0)
        current.append(unit)
        length += added
    if current:
        chunks.append(" ".join(current))
    return chunks


def chunk_pages(
    pages: Sequence[PageText],
    *,
    user_id: str,
    file_id: str,
    chunk_size: int = 500,
    overlap: int = 80,
) -> list[Chunk]:
    if chunk_size < 50:
        raise ValueError("chunk_size must be >= 50")
    if not 0 <= overlap < chunk_size // 2:
        raise ValueError("overlap must be >= 0 and < chunk_size / 2")

    chunks: list[Chunk] = []
    for page in pages:
        for text in _split_page(page.text, chunk_size, overlap):
            index = len(chunks)
            chunks.append(
                Chunk(
                    chunk_id=make_chunk_id(user_id, file_id, index),
                    text=text,
                    page_number=page.page_number,
                    chunk_index=index,
                )
            )
    return chunks
