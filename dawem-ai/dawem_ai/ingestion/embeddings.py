"""Local embeddings (no API calls, no cost).

``Embedder`` is the only interface the rest of the package uses, so tests inject a fake
and a different backend (e.g. Bedrock Titan) is one new class. Vectors are L2-normalized
so cosine similarity == dot product and the Chroma cosine space yields scores in [0, 1].
"""

from __future__ import annotations

import os
from typing import Protocol, Sequence, runtime_checkable

__all__ = ["Embedder", "SentenceTransformerEmbedder", "DEFAULT_EMBEDDING_MODEL"]

DEFAULT_EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


@runtime_checkable
class Embedder(Protocol):
    model_name: str

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class SentenceTransformerEmbedder:
    """``sentence-transformers`` model, loaded lazily on first use."""

    def __init__(self, model_name: str = DEFAULT_EMBEDDING_MODEL, batch_size: int = 32) -> None:
        self.model_name = model_name
        self.batch_size = batch_size
        self._model = None

    @classmethod
    def from_env(cls) -> SentenceTransformerEmbedder:
        return cls(os.environ.get("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL))

    def _load(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:  # pragma: no cover
                raise ImportError(
                    "sentence-transformers is not installed. "
                    "Run: pip install sentence-transformers"
                ) from exc
            self._model = SentenceTransformer(self.model_name)
        return self._model

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = self._load().encode(
            list(texts),
            batch_size=self.batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return vectors.tolist()

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]
