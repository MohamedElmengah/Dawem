"""Grounded retrieval: ownership check -> user-filtered search -> relevance gate.

Order matters and is the security/grounding contract:

1. **Ownership** - every requested ``file_id`` must already be indexed under the
   requesting ``user_id``. A file that exists for another user raises ``OwnershipError``
   (HTTP 403) and one that does not exist raises ``FileNotIndexedError`` (HTTP 404), as
   the project documentation's error table specifies. ``user_id`` comes from the
   verified Cognito identity on the backend, so end users cannot probe ids through this
   service. Nothing is embedded or searched until this passes.
2. **Isolated search** - the vector search is filtered by ``user_id`` AND the requested
   ``file_ids`` inside the store; a post-check drops (and logs) any hit that violates
   that anyway, so a store bug fails closed instead of leaking another user's text.
3. **Grounding rule** - hits below ``relevance_threshold`` are discarded; if nothing is
   left, ``InsufficientContextError`` is raised (HTTP 422). This happens before any LLM
   call, so the model never gets the chance to answer from thin air.

Synchronous (embedding + Chroma are blocking); wrap in ``asyncio.to_thread`` from async code.
Query text is never logged: it can contain study material.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Sequence

from dawem_ai.exceptions import FileNotIndexedError, InsufficientContextError, OwnershipError
from dawem_ai.ingestion.embeddings import Embedder
from dawem_ai.ingestion.store import ChunkStore

__all__ = ["Retriever", "RetrievedChunk"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    """A chunk handed to the LLM, with everything needed to build a citation."""

    chunk_id: str
    file_id: str
    file_name: str
    page_number: int | None  # None for non-paginated notes
    text: str
    score: float  # cosine similarity in [0, 1]


class Retriever:
    def __init__(
        self,
        store: ChunkStore,
        embedder: Embedder,
        *,
        top_k: int = 8,
        relevance_threshold: float = 0.35,
    ) -> None:
        if top_k < 1:
            raise ValueError("top_k must be >= 1")
        if not 0.0 <= relevance_threshold <= 1.0:
            raise ValueError("relevance_threshold must be within [0, 1]")
        self.store = store
        self.embedder = embedder
        self.top_k = top_k
        self.relevance_threshold = relevance_threshold

    @classmethod
    def from_env(cls, store: ChunkStore, embedder: Embedder) -> Retriever:
        return cls(
            store,
            embedder,
            top_k=int(os.environ.get("RETRIEVAL_TOP_K", "8")),
            relevance_threshold=float(os.environ.get("RELEVANCE_THRESHOLD", "0.35")),
        )

    def _require_owned(self, user_id: str, file_ids: Sequence[str]) -> None:
        """403 for a file owned by someone else, 404 for one that does not exist."""
        for file_id in file_ids:
            if self.store.owns(user_id=user_id, file_id=file_id):
                continue
            if self.store.is_claimed_by_other(user_id=user_id, file_id=file_id):
                raise OwnershipError(user_id=user_id, file_id=file_id)
            raise FileNotIndexedError(user_id=user_id, file_id=file_id)

    def retrieve(
        self, *, user_id: str, file_ids: Sequence[str], query: str
    ) -> list[RetrievedChunk]:
        """Return relevant chunks (best first) or raise.

        Raises:
            ValueError: empty user_id / file_ids / query (caller bug, not a 4xx).
            OwnershipError: some file_id belongs to another user (403).
            FileNotIndexedError: some file_id is not indexed at all (404).
            InsufficientContextError: nothing scored at or above the threshold.
        """
        if not user_id or not user_id.strip():
            raise ValueError("user_id is required")
        if not query or not query.strip():
            raise ValueError("query must be non-empty")
        unique_ids = list(dict.fromkeys(file_ids))  # de-dupe, keep order
        if not unique_ids or any(not f or not f.strip() for f in unique_ids):
            raise ValueError("file_ids must contain at least one non-empty id")

        # 1. Ownership: fail before spending an embedding call.
        self._require_owned(user_id, unique_ids)

        # 2. User-filtered similarity search.
        hits = self.store.query(
            self.embedder.embed_query(query),
            user_id=user_id,
            file_ids=unique_ids,
            top_k=self.top_k,
        )

        allowed = set(unique_ids)
        safe = []
        for hit in hits:
            if hit.user_id != user_id or hit.file_id not in allowed:
                logger.error(
                    "isolation violation: store returned chunk_id=%s outside the "
                    "requested user/files; dropped",
                    hit.chunk_id,
                )
                continue
            safe.append(hit)

        # 3. Grounding rule.
        relevant = sorted(
            (h for h in safe if h.score >= self.relevance_threshold),
            key=lambda h: h.score,
            reverse=True,
        )
        if not relevant:
            best = max((h.score for h in safe), default=None)
            logger.warning(
                "insufficient context: retrieved=%d best_score=%s threshold=%.2f",
                len(safe),
                f"{best:.3f}" if best is not None else "n/a",
                self.relevance_threshold,
            )
            raise InsufficientContextError(
                retrieved=len(safe), best_score=best, threshold=self.relevance_threshold
            )

        return [
            RetrievedChunk(
                chunk_id=h.chunk_id,
                file_id=h.file_id,
                file_name=h.file_name,
                page_number=h.page_number,
                text=h.text,
                score=h.score,
            )
            for h in relevant
        ]

    def retrieve_representative(
        self,
        *,
        user_id: str,
        file_ids: Sequence[str],
        max_chunks: int = 12,
        pool_size: int = 300,
        min_chars: int = 100,
    ) -> list[RetrievedChunk]:
        """Broad, topic-free sample of the user's selected files (for general quizzes).

        Same ownership check and isolation post-filter as ``retrieve``. There is no query,
        so there is nothing to be "relevant" to and the similarity threshold is not used:
        the grounding rule here is "the selected files must actually contain text".
        Chunks are spread evenly through each file (round-robin across files) and
        returned in reading order, so the sample covers the material rather than
        clustering around whatever a probe query happens to match.

        Raises:
            ValueError: bad arguments (caller bug).
            OwnershipError: some file_id belongs to another user (403).
            FileNotIndexedError: some file_id is not indexed at all (404).
            InsufficientContextError: no chunks, or fewer than ``min_chars`` of text.
        """
        if not user_id or not user_id.strip():
            raise ValueError("user_id is required")
        unique_ids = list(dict.fromkeys(file_ids))
        if not unique_ids or any(not f or not f.strip() for f in unique_ids):
            raise ValueError("file_ids must contain at least one non-empty id")
        if max_chunks < 1 or pool_size < max_chunks:
            raise ValueError("need 1 <= max_chunks <= pool_size")

        self._require_owned(user_id, unique_ids)

        # The probe only enumerates the user's chunks; its similarity scores are ignored.
        hits = self.store.query(
            self.embedder.embed_query(_BROAD_PROBE),
            user_id=user_id,
            file_ids=unique_ids,
            top_k=pool_size,
        )
        allowed = set(unique_ids)
        safe = []
        for hit in hits:
            if hit.user_id != user_id or hit.file_id not in allowed:
                logger.error(
                    "isolation violation: store returned chunk_id=%s outside the "
                    "requested user/files; dropped",
                    hit.chunk_id,
                )
                continue
            safe.append(hit)

        if not safe:
            logger.warning("insufficient context: no chunks found for broad retrieval")
            raise InsufficientContextError(retrieved=0)

        chosen = _spread_select(safe, unique_ids, max_chunks)
        if sum(len(h.text) for h in chosen) < min_chars:
            logger.warning(
                "insufficient context: broad retrieval found only %d chunk(s), too little text",
                len(chosen),
            )
            raise InsufficientContextError(retrieved=len(chosen))

        return [
            RetrievedChunk(
                chunk_id=h.chunk_id,
                file_id=h.file_id,
                file_name=h.file_name,
                page_number=h.page_number,
                text=h.text,
                score=h.score,  # similarity to the probe: NOT a relevance signal
            )
            for h in chosen
        ]


# Used only to enumerate a user's chunks in ``retrieve_representative``.
_BROAD_PROBE = "main ideas, key concepts, definitions and important facts"


def _spread_select(hits: Sequence, file_order: Sequence[str], max_chunks: int) -> list:
    """Pick up to ``max_chunks`` hits spread evenly through every file, in reading order."""
    groups: dict[str, list] = {f: [] for f in file_order}
    for hit in hits:
        groups[hit.file_id].append(hit)
    for group in groups.values():
        group.sort(key=lambda h: (h.page_number or 0, h.chunk_id))

    files = [f for f in file_order if groups[f]]
    quota = {f: 0 for f in files}
    remaining = max_chunks
    while remaining > 0 and any(quota[f] < len(groups[f]) for f in files):
        for f in files:
            if remaining == 0:
                break
            if quota[f] < len(groups[f]):
                quota[f] += 1
                remaining -= 1

    chosen = []
    for f in files:
        n, q = len(groups[f]), quota[f]
        chosen.extend(groups[f][(2 * i + 1) * n // (2 * q)] for i in range(q))
    return chosen
