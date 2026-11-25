"""Ingestion: files -> chunks -> local embeddings -> ChromaDB (tagged with user_id/file_id)."""

from dawem_ai.ingestion.chunker import Chunk, chunk_pages, make_chunk_id
from dawem_ai.ingestion.embeddings import Embedder, SentenceTransformerEmbedder
from dawem_ai.ingestion.indexer import IndexingResult, IndexingService
from dawem_ai.ingestion.loaders import (
    PageText,
    load_document,
    load_notes,
    load_pdf,
    load_text_file,
)
from dawem_ai.ingestion.s3_source import Boto3S3Fetcher, S3Fetcher
from dawem_ai.ingestion.store import ChromaChunkStore, ChunkRecord, ChunkStore, ScoredChunk

__all__ = [
    "Boto3S3Fetcher",
    "Chunk",
    "ChromaChunkStore",
    "ChunkRecord",
    "ChunkStore",
    "Embedder",
    "IndexingResult",
    "IndexingService",
    "PageText",
    "S3Fetcher",
    "ScoredChunk",
    "SentenceTransformerEmbedder",
    "chunk_pages",
    "load_document",
    "load_notes",
    "load_pdf",
    "load_text_file",
    "make_chunk_id",
]
