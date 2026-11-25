"""Error mapping: domain exceptions -> HTTP status + ``ErrorResponse`` body.

    InsufficientContextError      -> 422  insufficient_context   (the ONLY 422 we emit)
    OwnershipError                -> 403  forbidden              (file belongs to another user)
    FileNotIndexedError           -> 404  file_not_found         (file does not exist)
    SourceObjectNotFoundError     -> 404  file_not_found         (S3 object does not exist)
    StructuredOutputError         -> 502  invalid_model_output
    ProviderRateLimitError        -> 429  rate_limited           (client retries with backoff)
    ProviderError (any other)     -> 503  provider_unavailable
    SourceStorageError            -> 503  indexing_failed        (S3 read failed)
    UnsupportedFileTypeError      -> 415  invalid_file
    DocumentLoadError/EmptyDocumentError -> 400 invalid_file
    PayloadTooLargeError          -> 413  invalid_file
    IndexingFailedError           -> 503  indexing_failed
    UnauthorizedError             -> 401  unauthorized
    request validation            -> 400  invalid_request        (no input echoed back)
    anything else                 -> 500  internal_error         (no details leaked)

Bodies never contain prompts, study text or request input. Unexpected exceptions are
logged by type only, because provider/SDK messages could embed prompt fragments.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from dawem_ai.exceptions import (
    DocumentLoadError,
    EmptyDocumentError,
    FileNotIndexedError,
    IndexingFailedError,
    InsufficientContextError,
    OwnershipError,
    ProviderError,
    ProviderRateLimitError,
    SourceObjectNotFoundError,
    SourceStorageError,
    StructuredOutputError,
    UnsupportedFileTypeError,
)
from dawem_ai.schemas.contracts import ErrorCode, ErrorResponse

logger = logging.getLogger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"


class UnauthorizedError(Exception):
    """Missing or wrong ``X-API-Key``."""


class InvalidRequestError(Exception):
    """A request field failed a check that FastAPI's own validation cannot express."""


class PayloadTooLargeError(Exception):
    """Upload exceeded the configured size limit."""


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _json(request: Request, status: int, code: ErrorCode, detail: str) -> JSONResponse:
    rid = _request_id(request)
    body = ErrorResponse(code=code, detail=detail, request_id=rid)
    return JSONResponse(
        status_code=status,
        content=body.model_dump(mode="json"),
        headers={REQUEST_ID_HEADER: rid} if rid else None,
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(InsufficientContextError)
    async def _insufficient(request: Request, exc: InsufficientContextError):
        rid = _request_id(request)
        return JSONResponse(
            status_code=422,
            content=ErrorResponse.insufficient_context(request_id=rid).model_dump(mode="json"),
            headers={REQUEST_ID_HEADER: rid} if rid else None,
        )

    @app.exception_handler(OwnershipError)
    async def _ownership(request: Request, exc: OwnershipError):
        # Project documentation s.17: 403 = the user does not own the requested resource.
        return _json(request, 403, ErrorCode.FORBIDDEN, "You do not own this file")

    @app.exception_handler(FileNotIndexedError)
    @app.exception_handler(SourceObjectNotFoundError)
    async def _not_found(request: Request, exc: Exception):
        # Project documentation s.17: 404 = the file does not exist.
        return _json(request, 404, ErrorCode.FILE_NOT_FOUND, "File not found")

    @app.exception_handler(ProviderRateLimitError)
    async def _rate_limited(request: Request, exc: ProviderRateLimitError):
        # Project documentation s.17: 429 = rate limited, client retries with backoff.
        return _json(
            request, 429, ErrorCode.RATE_LIMITED,
            "AI provider rate limit reached; retry with backoff",
        )

    @app.exception_handler(StructuredOutputError)
    async def _structured(request: Request, exc: StructuredOutputError):
        return _json(
            request, 502, ErrorCode.INVALID_MODEL_OUTPUT,
            "The AI provider returned output that could not be validated",
        )

    @app.exception_handler(ProviderError)
    async def _provider(request: Request, exc: ProviderError):
        # Covers ProviderUnavailableError and plain ProviderError (rate limits are 429 above).
        return _json(
            request, 503, ErrorCode.PROVIDER_UNAVAILABLE,
            "AI provider is temporarily unavailable",
        )

    @app.exception_handler(UnsupportedFileTypeError)
    async def _unsupported(request: Request, exc: UnsupportedFileTypeError):
        return _json(
            request, 415, ErrorCode.INVALID_FILE,
            "Unsupported file type; supported: .pdf, .txt, .md",
        )

    @app.exception_handler(DocumentLoadError)
    @app.exception_handler(EmptyDocumentError)
    async def _bad_document(request: Request, exc: Exception):
        return _json(request, 400, ErrorCode.INVALID_FILE, str(exc))

    @app.exception_handler(PayloadTooLargeError)
    async def _too_large(request: Request, exc: PayloadTooLargeError):
        return _json(request, 413, ErrorCode.INVALID_FILE, "File is too large")

    @app.exception_handler(IndexingFailedError)
    @app.exception_handler(SourceStorageError)
    async def _indexing(request: Request, exc: Exception):
        return _json(
            request, 503, ErrorCode.INDEXING_FAILED,
            "Indexing failed; please retry shortly",
        )

    @app.exception_handler(UnauthorizedError)
    async def _unauthorized(request: Request, exc: UnauthorizedError):
        return _json(request, 401, ErrorCode.UNAUTHORIZED, "Missing or invalid API key")

    @app.exception_handler(InvalidRequestError)
    async def _invalid(request: Request, exc: InvalidRequestError):
        return _json(request, 400, ErrorCode.INVALID_REQUEST, str(exc))

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        # Field path + rule only. FastAPI's default body echoes the submitted input, which
        # for notes/goals is the user's own study text.
        parts = []
        for err in exc.errors()[:10]:
            loc = ".".join(str(p) for p in err.get("loc", ()) if p not in ("body", "query"))
            parts.append(f"{loc or 'request'}: {err.get('msg', 'invalid')}")
        return _json(request, 400, ErrorCode.INVALID_REQUEST, "; ".join(parts))

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception):
        logger.error("unhandled %s in %s", type(exc).__name__, request.url.path)
        return _json(request, 500, ErrorCode.INTERNAL_ERROR, "Internal server error")
