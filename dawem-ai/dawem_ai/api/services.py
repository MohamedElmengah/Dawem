"""Wiring: build the real object graph from environment variables.

Tests (and the team's integration tests) construct ``Services`` directly with fakes.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from dawem_ai.ingestion import (
    Boto3S3Fetcher,
    ChromaChunkStore,
    IndexingService,
    S3Fetcher,
    SentenceTransformerEmbedder,
)
from dawem_ai.llm.provider import AiProvider, FallbackProvider, GeminiProvider, GroqProvider
from dawem_ai.pipelines import PlanPipeline, QuizPipeline
from dawem_ai.retrieval import Retriever

__all__ = ["Services", "build_provider_from_env", "build_services_from_env"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Services:
    indexer: IndexingService
    plans: PlanPipeline
    quizzes: QuizPipeline
    s3: S3Fetcher | None = None  # only needed by POST /files/index-s3


def build_provider_from_env() -> AiProvider:
    """Gemini first, Groq as fallback; either alone works if only one key is set."""
    providers: list[AiProvider] = []
    if os.environ.get("GEMINI_API_KEY"):
        providers.append(GeminiProvider.from_env())
    if os.environ.get("GROQ_API_KEY"):
        providers.append(GroqProvider.from_env())
    if not providers:
        raise ValueError("Set GEMINI_API_KEY and/or GROQ_API_KEY to start the service")
    return providers[0] if len(providers) == 1 else FallbackProvider(providers)


def build_services_from_env() -> Services:
    store = ChromaChunkStore(os.environ.get("CHROMA_PERSIST_DIR", "./.chroma"))
    embedder = SentenceTransformerEmbedder.from_env()  # loads its model lazily
    provider = build_provider_from_env()
    threshold = float(os.environ.get("RELEVANCE_THRESHOLD", "0.35"))

    # Plans and quizzes want more context than a single-question lookup.
    plan_retriever = Retriever(
        store, embedder,
        top_k=int(os.environ.get("PLAN_TOP_K", "16")),
        relevance_threshold=threshold,
    )
    quiz_retriever = Retriever(
        store, embedder,
        top_k=int(os.environ.get("QUIZ_TOP_K", "12")),
        relevance_threshold=threshold,
    )
    return Services(
        indexer=IndexingService.from_env(store, embedder),
        plans=PlanPipeline(
            plan_retriever,
            provider,
            batch_days=int(os.environ.get("PLAN_BATCH_DAYS", "7")),
        ),
        quizzes=QuizPipeline(
            quiz_retriever,
            provider,
            broad_chunks=int(os.environ.get("QUIZ_BROAD_CHUNKS", "12")),
        ),
        s3=Boto3S3Fetcher(),  # boto3 is imported on first use, not at startup
    )
