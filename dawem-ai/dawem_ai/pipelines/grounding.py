"""Prompt-side labels and response-side citation hydration, shared by both pipelines.

The model never sees (or has to reproduce) 32-char chunk ids: each retrieved chunk gets a
short label ``C1``, ``C2``, ... that it cites in ``source_chunk_ids``. Afterwards the
pipeline resolves every cited label (or raw chunk id) against the chunks it actually
retrieved. Anything that does not resolve is discarded, and the ``SourceCitation`` is
built from our own records (file name, page, chunk id), never from model output. A task
or question left with zero resolvable sources is not allowed to survive.
"""

from __future__ import annotations

from typing import Iterable, Sequence

from dawem_ai.retrieval import RetrievedChunk
from dawem_ai.schemas.contracts import SourceCitation

__all__ = ["ChunkIndex", "to_citation", "SNIPPET_CHARS"]

SNIPPET_CHARS = 200
_UNTITLED = "Untitled"


class ChunkIndex:
    """Retrieved chunks addressable by prompt label (``C1``) or real ``chunk_id``."""

    def __init__(self, chunks: Sequence[RetrievedChunk]) -> None:
        self.chunks: list[RetrievedChunk] = list(chunks)
        self.labels: list[str] = [f"C{i}" for i in range(1, len(self.chunks) + 1)]
        self._lookup: dict[str, RetrievedChunk] = {}
        for label, chunk in zip(self.labels, self.chunks):
            self._lookup[label] = chunk
            self._lookup[chunk.chunk_id] = chunk

    def render_context(self) -> str:
        """The numbered context block placed in the prompt (data, not instructions)."""
        blocks = []
        for label, chunk in zip(self.labels, self.chunks):
            header = f"[{label}]" + (
                f" (page {chunk.page_number})" if chunk.page_number is not None else ""
            )
            # Study material is untrusted text: stop it from closing our delimiter.
            body = chunk.text.replace("</context>", "").replace("<context>", "")
            blocks.append(f"{header}\n{body}")
        return "<context>\n" + "\n\n".join(blocks) + "\n</context>"

    def label_range(self) -> str:
        return f"{self.labels[0]}..{self.labels[-1]}" if self.labels else "(none)"

    def resolve(self, cited: Iterable[str]) -> list[RetrievedChunk]:
        """Real chunks for the cited ids, de-duplicated, in citation order.

        Unknown / hallucinated ids are silently dropped here; callers decide what a
        task or question with *no* resolvable source means.
        """
        seen: set[str] = set()
        out: list[RetrievedChunk] = []
        for raw in cited:
            if not isinstance(raw, str):
                continue
            key = raw.strip().strip("[]()").strip()
            chunk = self._lookup.get(key) or self._lookup.get(key.upper())
            if chunk is None or chunk.chunk_id in seen:
                continue
            seen.add(chunk.chunk_id)
            out.append(chunk)
        return out


def to_citation(chunk: RetrievedChunk, *, with_score: bool = True) -> SourceCitation:
    """Build a citation from retrieval metadata only.

    ``with_score=False`` is used for topic-less (broad) quizzes, where a similarity
    score would not mean "relevance to anything".
    """
    snippet = " ".join(chunk.text.split())[:SNIPPET_CHARS] or None
    return SourceCitation(
        chunk_id=chunk.chunk_id,
        file_id=chunk.file_id,
        file_name=(chunk.file_name or _UNTITLED)[:255],
        page_number=chunk.page_number,
        snippet=snippet,
        score=round(chunk.score, 4) if with_score else None,
    )
