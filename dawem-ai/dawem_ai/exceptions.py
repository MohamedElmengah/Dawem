"""Domain exceptions for the Dawem AI backend.

The API layer is the only place that maps these to HTTP status codes:

    InsufficientContextError  -> 422  {"code": "insufficient_context", ...}
    OwnershipError            -> 403  (file exists but belongs to another user)
    FileNotIndexedError       -> 404  (file does not exist / is not indexed)
    SourceObjectNotFoundError -> 404  (S3 object does not exist)
    ProviderRateLimitError    -> 429
    StructuredOutputError     -> 502
    ProviderError (other)     -> 503
    IngestionError (and subclasses) -> decided by the indexing endpoint
"""

from __future__ import annotations

INSUFFICIENT_CONTEXT_DETAIL = "Insufficient source context"


class DawemAIError(Exception):
    """Base class for all controlled errors raised by this package."""


# --------------------------------------------------------------------------- #
# Grounding / ownership
# --------------------------------------------------------------------------- #
class InsufficientContextError(DawemAIError):
    """Retrieval found nothing relevant enough in the user's own files.

    Raised *before* any LLM call so the model never gets a chance to guess.
    The diagnostic attributes are for logs only; they must not be returned to
    clients (the HTTP body is just ``INSUFFICIENT_CONTEXT_DETAIL``).
    """

    def __init__(
        self,
        *,
        retrieved: int = 0,
        best_score: float | None = None,
        threshold: float | None = None,
    ) -> None:
        super().__init__(INSUFFICIENT_CONTEXT_DETAIL)
        self.retrieved = retrieved
        self.best_score = best_score
        self.threshold = threshold


class OwnershipError(DawemAIError):
    """A file_id exists but belongs to a different user_id (HTTP 403)."""

    def __init__(self, *, user_id: str, file_id: str) -> None:
        super().__init__("File does not belong to user")
        self.user_id = user_id
        self.file_id = file_id


class FileNotIndexedError(DawemAIError):
    """No chunks exist for this file_id at all: unknown or not indexed yet (HTTP 404)."""

    def __init__(self, *, user_id: str, file_id: str) -> None:
        super().__init__("File not found")
        self.user_id = user_id
        self.file_id = file_id


# --------------------------------------------------------------------------- #
# LLM providers
# --------------------------------------------------------------------------- #
class ProviderError(DawemAIError):
    """A provider call failed. Non-retryable by default (bad key, bad request)."""


class ProviderRateLimitError(ProviderError):
    """Quota / rate limit hit (HTTP 429). Retryable, then fall back."""


class ProviderUnavailableError(ProviderError):
    """Timeout or 5xx. Retryable, then fall back."""


class StructuredOutputError(ProviderError):
    """The model kept returning output that failed schema validation."""


# --------------------------------------------------------------------------- #
# Ingestion
# --------------------------------------------------------------------------- #
class IngestionError(DawemAIError):
    """Base class for failures while turning an uploaded file into indexed chunks."""


class UnsupportedFileTypeError(IngestionError):
    """The file extension is not one of the supported loaders (pdf, txt, md)."""


class DocumentLoadError(IngestionError):
    """The file could not be read (corrupt, encrypted, not valid UTF-8, ...)."""


class EmptyDocumentError(IngestionError):
    """The file loaded but contains no extractable text (e.g. a scanned PDF)."""


class IndexingFailedError(IngestionError):
    """Embedding/storage kept failing after all retries. ``__cause__`` has the last error."""

    def __init__(self, *, file_id: str, attempts: int) -> None:
        super().__init__(f"Indexing failed for file after {attempts} attempt(s)")
        self.file_id = file_id
        self.attempts = attempts


class SourceObjectNotFoundError(IngestionError):
    """The S3 object named in an ``/files/index-s3`` request does not exist."""


class SourceStorageError(IngestionError):
    """The source storage (S3) could not be read: access denied, throttled, network."""
