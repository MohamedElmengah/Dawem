"""Indexing service: load -> chunk -> embed -> store, with retries.

Synchronous on purpose (CPU-bound embedding, blocking Chroma I/O). From FastAPI call it
via ``asyncio.to_thread`` / ``run_in_threadpool`` or from a background worker.

Failure handling
* Permanent problems (unsupported/empty/corrupt file, file_id owned by another user) are
  ``DawemAIError``s: raised immediately, never retried.
* Anything else during embed/store is treated as transient and retried with exponential
  backoff + jitter, up to ``max_attempts``. After the last attempt the partial chunks of
  this file are removed (no half-indexed files) and ``IndexingFailedError`` is raised.
* Re-indexing the same (user_id, file_id) replaces the previous chunks.
"""

from __future__ import annotations

import logging
import os
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from dawem_ai.exceptions import (
    DawemAIError,
    FileNotIndexedError,
    IndexingFailedError,
    OwnershipError,
)
from dawem_ai.ingestion.chunker import Chunk, chunk_pages
from dawem_ai.ingestion.embeddings import Embedder
from dawem_ai.ingestion.loaders import PageText, load_document, load_notes
from dawem_ai.ingestion.store import ChunkRecord, ChunkStore

__all__ = ["IndexingService", "IndexingResult"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class IndexingResult:
    file_id: str
    chunk_count: int
    attempts: int


class IndexingService:
    def __init__(
        self,
        store: ChunkStore,
        embedder: Embedder,
        *,
        chunk_size: int = 500,
        chunk_overlap: int = 80,
        max_attempts: int = 3,
        backoff_base_s: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.store = store
        self.embedder = embedder
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.max_attempts = max(1, max_attempts)
        self.backoff_base_s = backoff_base_s
        self._sleep = sleep

    @classmethod
    def from_env(cls, store: ChunkStore, embedder: Embedder) -> IndexingService:
        return cls(
            store,
            embedder,
            chunk_size=int(os.environ.get("CHUNK_SIZE", "500")),
            chunk_overlap=int(os.environ.get("CHUNK_OVERLAP", "80")),
            max_attempts=int(os.environ.get("INDEX_MAX_ATTEMPTS", "3")),
            backoff_base_s=float(os.environ.get("INDEX_BACKOFF_BASE_S", "1.0")),
        )

    # ---- public API --------------------------------------------------------- #
    def index_file(
        self,
        path: str | Path,
        *,
        user_id: str,
        file_id: str,
        file_name: str | None = None,
    ) -> IndexingResult:
        """Index a PDF / .txt / .md file. ``file_name`` is what the UI shows in
        citations; pass the original upload name if ``path`` is a temp file."""
        self._require_ids(user_id, file_id)
        return self._index(
            load_document(path),
            user_id=user_id,
            file_id=file_id,
            file_name=file_name or Path(path).name,
        )

    def index_notes(
        self, text: str, *, user_id: str, file_id: str, file_name: str
    ) -> IndexingResult:
        """Index notes typed or pasted in the app."""
        self._require_ids(user_id, file_id)
        return self._index(
            load_notes(text), user_id=user_id, file_id=file_id, file_name=file_name
        )

    def delete_file(self, *, user_id: str, file_id: str) -> None:
        """Remove every chunk of ``file_id`` (file deletion / account cleanup).

        Raises ``OwnershipError`` (403) if the file belongs to another user and
        ``FileNotIndexedError`` (404) if it does not exist.
        """
        self._require_ids(user_id, file_id)
        if not self.store.owns(user_id=user_id, file_id=file_id):
            if self.store.is_claimed_by_other(user_id=user_id, file_id=file_id):
                raise OwnershipError(user_id=user_id, file_id=file_id)
            raise FileNotIndexedError(user_id=user_id, file_id=file_id)
        self.store.delete_file(user_id=user_id, file_id=file_id)

    # ---- internals ---------------------------------------------------------- #
    @staticmethod
    def _require_ids(user_id: str, file_id: str) -> None:
        if not user_id or not user_id.strip() or not file_id or not file_id.strip():
            raise ValueError("user_id and file_id are required")

    def _index(
        self, pages: list[PageText], *, user_id: str, file_id: str, file_name: str
    ) -> IndexingResult:
        # A file_id already indexed for someone else must never be overwritten/extended.
        if self.store.is_claimed_by_other(user_id=user_id, file_id=file_id):
            raise OwnershipError(user_id=user_id, file_id=file_id)

        chunks = chunk_pages(
            pages,
            user_id=user_id,
            file_id=file_id,
            chunk_size=self.chunk_size,
            overlap=self.chunk_overlap,
        )

        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                self._embed_and_store(
                    chunks, user_id=user_id, file_id=file_id, file_name=file_name
                )
                return IndexingResult(
                    file_id=file_id, chunk_count=len(chunks), attempts=attempt
                )
            except DawemAIError:
                raise  # permanent: retrying cannot help
            except Exception as exc:  # noqa: BLE001 - embed/store failures are transient
                last_error = exc
                self._cleanup(user_id=user_id, file_id=file_id)
                logger.warning(
                    "indexing file_id=%s failed (%s), attempt %d/%d",
                    file_id,
                    type(exc).__name__,
                    attempt,
                    self.max_attempts,
                )
                if attempt < self.max_attempts:
                    delay = self.backoff_base_s * (2 ** (attempt - 1))
                    delay += random.uniform(0, 0.25 * self.backoff_base_s)
                    self._sleep(delay)

        raise IndexingFailedError(file_id=file_id, attempts=self.max_attempts) from last_error

    def _embed_and_store(
        self, chunks: list[Chunk], *, user_id: str, file_id: str, file_name: str
    ) -> None:
        vectors = self.embedder.embed_documents([c.text for c in chunks])
        if len(vectors) != len(chunks):
            raise RuntimeError("embedder returned a different number of vectors than chunks")
        records = [
            ChunkRecord(
                chunk_id=c.chunk_id,
                user_id=user_id,
                file_id=file_id,
                file_name=file_name,
                text=c.text,
                embedding=vec,
                chunk_index=c.chunk_index,
                page_number=c.page_number,
                embedding_model=self.embedder.model_name,
            )
            for c, vec in zip(chunks, vectors)
        ]
        # Replace any previous version of this file, then write the new one.
        self.store.delete_file(user_id=user_id, file_id=file_id)
        self.store.add(records)

    def _cleanup(self, *, user_id: str, file_id: str) -> None:
        try:
            self.store.delete_file(user_id=user_id, file_id=file_id)
        except Exception:  # noqa: BLE001 - best effort; the retry/replace path re-deletes
            logger.exception("cleanup of partial index failed for file_id=%s", file_id)
