"""Vector storage: the ``ChunkStore`` interface and its ChromaDB implementation.

Tenant isolation is enforced here, at the lowest layer: there is deliberately no query
method without ``user_id``, and every chunk is rejected at construction unless it carries
non-empty ``user_id`` and ``file_id``. All users share one collection; the metadata
filter is the isolation boundary, so it must never be optional.

Scores are cosine similarity in [0, 1] (Chroma returns cosine *distance* = 1 - similarity
for the ``cosine`` space; vectors are normalized by the embedder).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, Sequence

__all__ = ["ChunkRecord", "ScoredChunk", "ChunkStore", "ChromaChunkStore"]

DEFAULT_COLLECTION = "dawem_chunks"


@dataclass(frozen=True, slots=True)
class ChunkRecord:
    """A chunk ready to be stored. ``user_id`` / ``file_id`` are mandatory."""

    chunk_id: str
    user_id: str
    file_id: str
    file_name: str
    text: str
    embedding: list[float]
    chunk_index: int
    page_number: int | None = None  # None for non-paginated notes
    embedding_model: str = ""  # lets a future re-embedding migration find stale chunks

    def __post_init__(self) -> None:
        for name in ("chunk_id", "user_id", "file_id", "text"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"ChunkRecord.{name} must be a non-empty string")

    def metadata(self) -> dict[str, str | int]:
        meta: dict[str, str | int] = {
            "user_id": self.user_id,
            "file_id": self.file_id,
            "file_name": self.file_name,
            "chunk_index": self.chunk_index,
            "embedding_model": self.embedding_model,
        }
        if self.page_number is not None:  # Chroma metadata cannot hold None
            meta["page_number"] = self.page_number
        return meta


@dataclass(frozen=True, slots=True)
class ScoredChunk:
    chunk_id: str
    user_id: str
    file_id: str
    file_name: str
    text: str
    score: float  # cosine similarity clamped to [0, 1]
    page_number: int | None = None


class ChunkStore(Protocol):
    def add(self, records: Sequence[ChunkRecord]) -> None: ...

    def delete_file(self, *, user_id: str, file_id: str) -> None: ...

    def owns(self, *, user_id: str, file_id: str) -> bool:
        """True iff at least one chunk exists with BOTH this user_id and file_id."""

    def is_claimed_by_other(self, *, user_id: str, file_id: str) -> bool:
        """True iff chunks for ``file_id`` exist that belong to a different user."""

    def query(
        self,
        embedding: Sequence[float],
        *,
        user_id: str,
        file_ids: Sequence[str],
        top_k: int,
    ) -> list[ScoredChunk]:
        """Nearest chunks among ``user_id``'s chunks of ``file_ids``, best first."""


def _where(*conditions: dict[str, Any]) -> dict[str, Any]:
    return conditions[0] if len(conditions) == 1 else {"$and": list(conditions)}


class ChromaChunkStore:
    """ChromaDB-backed store. Pass ``persist_dir`` for disk, or ``client`` (e.g. an
    ``EphemeralClient``) for tests."""

    def __init__(
        self,
        persist_dir: str | None = None,
        *,
        client: Any | None = None,
        collection_name: str = DEFAULT_COLLECTION,
    ) -> None:
        if client is None:
            try:
                import chromadb
            except ImportError as exc:  # pragma: no cover
                raise ImportError("chromadb is not installed. Run: pip install chromadb") from exc
            client = chromadb.PersistentClient(path=persist_dir or "./.chroma")
        self._collection = client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
            embedding_function=None,  # we always supply vectors; never let Chroma embed
        )

    # ---- writes ------------------------------------------------------------- #
    def add(self, records: Sequence[ChunkRecord]) -> None:
        if not records:
            return
        self._collection.upsert(
            ids=[r.chunk_id for r in records],
            embeddings=[r.embedding for r in records],
            documents=[r.text for r in records],
            metadatas=[r.metadata() for r in records],
        )

    def delete_file(self, *, user_id: str, file_id: str) -> None:
        self._collection.delete(
            where=_where({"user_id": user_id}, {"file_id": file_id}),
        )

    # ---- ownership ---------------------------------------------------------- #
    def owns(self, *, user_id: str, file_id: str) -> bool:
        found = self._collection.get(
            where=_where({"user_id": user_id}, {"file_id": file_id}), limit=1, include=[]
        )
        return bool(found["ids"])

    def is_claimed_by_other(self, *, user_id: str, file_id: str) -> bool:
        found = self._collection.get(
            where=_where({"file_id": file_id}, {"user_id": {"$ne": user_id}}),
            limit=1,
            include=[],
        )
        return bool(found["ids"])

    # ---- search ------------------------------------------------------------- #
    def query(
        self,
        embedding: Sequence[float],
        *,
        user_id: str,
        file_ids: Sequence[str],
        top_k: int,
    ) -> list[ScoredChunk]:
        if not user_id or not file_ids:
            raise ValueError("query requires a user_id and at least one file_id")
        result = self._collection.query(
            query_embeddings=[list(embedding)],
            n_results=top_k,
            where=_where({"user_id": user_id}, {"file_id": {"$in": list(file_ids)}}),
            include=["documents", "metadatas", "distances"],
        )
        hits: list[ScoredChunk] = []
        for chunk_id, text, meta, distance in zip(
            result["ids"][0],
            result["documents"][0],
            result["metadatas"][0],
            result["distances"][0],
        ):
            hits.append(
                ScoredChunk(
                    chunk_id=chunk_id,
                    user_id=meta["user_id"],
                    file_id=meta["file_id"],
                    file_name=meta.get("file_name", ""),
                    text=text,
                    score=max(0.0, min(1.0, 1.0 - float(distance))),
                    page_number=meta.get("page_number"),
                )
            )
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits
