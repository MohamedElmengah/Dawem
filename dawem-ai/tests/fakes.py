"""Offline test doubles shared by the ingestion / retrieval tests."""

from __future__ import annotations

import hashlib
import math
import re
from typing import Sequence

from dawem_ai.ingestion.store import ChunkRecord, ScoredChunk

_WORD_RE = re.compile(r"\w+", re.UNICODE)


class FakeEmbedder:
    """Deterministic bag-of-words hashing embedder (L2-normalized, cosine-ready).

    Texts sharing words get high similarity; texts with no shared words get ~0, which
    makes relevance-threshold behaviour easy to assert without downloading a model.
    """

    model_name = "fake-hashing-embedder"

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim
        self.query_calls = 0
        self.document_calls = 0

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for word in _WORD_RE.findall(text.lower()):
            bucket = int(hashlib.md5(word.encode()).hexdigest(), 16) % self.dim
            v[bucket] += 1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self.document_calls += 1
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        self.query_calls += 1
        return self._vec(text)


class InMemoryChunkStore:
    """Reference implementation of the ``ChunkStore`` protocol (cosine, same filters)."""

    def __init__(self) -> None:
        self.records: dict[str, ChunkRecord] = {}

    def add(self, records: Sequence[ChunkRecord]) -> None:
        for r in records:
            self.records[r.chunk_id] = r

    def delete_file(self, *, user_id: str, file_id: str) -> None:
        self.records = {
            k: r
            for k, r in self.records.items()
            if not (r.user_id == user_id and r.file_id == file_id)
        }

    def owns(self, *, user_id: str, file_id: str) -> bool:
        return any(r.user_id == user_id and r.file_id == file_id for r in self.records.values())

    def is_claimed_by_other(self, *, user_id: str, file_id: str) -> bool:
        return any(r.file_id == file_id and r.user_id != user_id for r in self.records.values())

    def query(self, embedding, *, user_id, file_ids, top_k) -> list[ScoredChunk]:
        if not user_id or not file_ids:
            raise ValueError("query requires a user_id and at least one file_id")
        wanted = set(file_ids)
        hits = []
        for r in self.records.values():
            if r.user_id != user_id or r.file_id not in wanted:
                continue
            sim = sum(a * b for a, b in zip(embedding, r.embedding))
            hits.append(
                ScoredChunk(
                    chunk_id=r.chunk_id,
                    user_id=r.user_id,
                    file_id=r.file_id,
                    file_name=r.file_name,
                    text=r.text,
                    score=max(0.0, min(1.0, sim)),
                    page_number=r.page_number,
                )
            )
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:top_k]


def make_pdf(pages: Sequence[str]) -> bytes:
    """Build a minimal valid text PDF (Helvetica). An empty string gives a blank page."""
    n = len(pages)
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(n))
    objs: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for i, text in enumerate(pages):
        content_id = 5 + 2 * i
        objs.append(
            (
                "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Contents {content_id} 0 R /Resources << /Font << /F1 3 0 R >> >> >>"
            ).encode()
        )
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode()
        objs.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode() + b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    ).encode()
    return bytes(out)
