# NOTE: no ``from __future__ import annotations`` here: FastAPI resolves these parameter
# annotations at import time.
import hmac
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Header, Path as PathParam, Query, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from dawem_ai.api.errors import InvalidRequestError, PayloadTooLargeError, UnauthorizedError
from dawem_ai.api.schemas import HealthResponse, IndexNotesRequest, IndexResponse, IndexS3Request
from dawem_ai.api.services import Services
from dawem_ai.exceptions import UnsupportedFileTypeError
from dawem_ai.ingestion.loaders import SUPPORTED_EXTENSIONS
from dawem_ai.schemas.contracts import ErrorResponse, Quiz, QuizRequest, StudyPlan, StudyPlanRequest

_READ_BLOCK = 1024 * 1024


def get_services(request: Request) -> Services:
    return request.app.state.services


async def require_api_key(request: Request, x_api_key: str | None = Header(default=None)):
    """The calling backend is trusted to supply user_id, so the service itself must only
    accept that backend. Disabled when DAWEM_AI_API_KEY is unset (local dev)."""
    expected = getattr(request.app.state, "api_key", "")
    if not expected:
        return
    if x_api_key is None or not hmac.compare_digest(x_api_key.encode(), expected.encode()):
        raise UnauthorizedError()


public = APIRouter()
protected = APIRouter(dependencies=[Depends(require_api_key)])


# --------------------------------------------------------------------------- #
@public.get("/health", response_model=HealthResponse)
async def health(request: Request):
    """Readiness: 200 once the services (vector store, embedder, providers) are wired."""
    if getattr(request.app.state, "services", None) is None:
        return JSONResponse(status_code=503, content={"status": "starting"})
    return HealthResponse(status="ok")


# --------------------------------------------------------------------------- #
@protected.post("/files/index", response_model=IndexResponse)
async def index_uploaded_file(
    request: Request,
    user_id: str = Form(..., min_length=1, max_length=128),
    file_id: str = Form(..., min_length=1, max_length=128),
    file: UploadFile = File(...),
    file_name: str | None = Form(default=None, max_length=255),
    services: Services = Depends(get_services),
):
    """Index an uploaded PDF / .txt / .md for ``user_id`` under ``file_id``.

    Re-indexing the same (user_id, file_id) replaces the previous chunks. A file_id that
    belongs to another user answers 403.
    """
    user_id, file_id = user_id.strip(), file_id.strip()
    if not user_id or not file_id:
        raise InvalidRequestError("user_id and file_id must not be blank")

    original = (file.filename or "").replace("\\", "/")
    suffix = Path(original).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise UnsupportedFileTypeError(suffix)
    display_name = ((file_name or "").strip() or Path(original).name or f"{file_id}{suffix}")[:255]

    limit = request.app.state.max_upload_bytes
    with tempfile.TemporaryDirectory(prefix="dawem-upload-") as tmp:
        path = Path(tmp) / f"upload{suffix}"  # never use the client's file name on disk
        written = 0
        with path.open("wb") as out:
            while block := await file.read(_READ_BLOCK):
                written += len(block)
                if written > limit:
                    raise PayloadTooLargeError()
                out.write(block)
        result = await run_in_threadpool(
            services.indexer.index_file,
            path,
            user_id=user_id,
            file_id=file_id,
            file_name=display_name,
        )
    return IndexResponse(file_id=result.file_id, chunk_count=result.chunk_count)


@protected.post("/files/index-s3", response_model=IndexResponse)
async def index_s3_file(
    request: Request, body: IndexS3Request, services: Services = Depends(get_services)
):
    """Index a PDF / .txt / .md that is already in S3 (alternative to the multipart upload).

    The service reads the object with its own AWS credentials (IAM role). Answers 404 if the
    object does not exist, 413 if it is larger than ``MAX_UPLOAD_MB``, 415 for an unsupported
    extension and 503 if S3 cannot be read. If ``DAWEM_S3_ALLOWED_BUCKETS`` is set, any other
    bucket is rejected with 400.
    """
    if services.s3 is None:
        raise RuntimeError("S3 fetcher is not configured")  # wiring bug -> 500
    allowed = request.app.state.allowed_s3_buckets
    if allowed and body.bucket not in allowed:
        raise InvalidRequestError("bucket is not allowed")

    key_name = body.s3_key.replace("\\", "/").rsplit("/", 1)[-1]
    suffix = Path(key_name).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise UnsupportedFileTypeError(suffix)
    display_name = ((body.file_name or "").strip() or key_name or f"{body.file_id}{suffix}")[:255]

    limit = request.app.state.max_upload_bytes
    size = await run_in_threadpool(services.s3.size, bucket=body.bucket, key=body.s3_key)
    if size > limit:
        raise PayloadTooLargeError()

    with tempfile.TemporaryDirectory(prefix="dawem-s3-") as tmp:
        path = Path(tmp) / f"download{suffix}"  # never use the S3 key as a local path
        await run_in_threadpool(
            services.s3.download, bucket=body.bucket, key=body.s3_key, dest=path
        )
        if path.stat().st_size > limit:  # object changed between HEAD and GET
            raise PayloadTooLargeError()
        result = await run_in_threadpool(
            services.indexer.index_file,
            path,
            user_id=body.user_id,
            file_id=body.file_id,
            file_name=display_name,
        )
    return IndexResponse(file_id=result.file_id, chunk_count=result.chunk_count)


@protected.delete("/files/{file_id}", status_code=204, responses={
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse, "description": "File belongs to another user"},
    404: {"model": ErrorResponse, "description": "File does not exist"},
})
async def delete_file(
    file_id: str = PathParam(..., min_length=1, max_length=128),
    user_id: str = Query(..., min_length=1, max_length=128),
    services: Services = Depends(get_services),
):
    """Remove every indexed chunk of ``file_id`` (call when the user deletes a learning file).

    204 on success; 403 if the file belongs to another user; 404 if it does not exist.
    """
    await run_in_threadpool(
        services.indexer.delete_file, user_id=user_id.strip(), file_id=file_id.strip()
    )
    return Response(status_code=204)


@protected.post("/files/index-notes", response_model=IndexResponse)
async def index_notes(body: IndexNotesRequest, services: Services = Depends(get_services)):
    """Index notes typed or pasted in the app."""
    result = await run_in_threadpool(
        services.indexer.index_notes,
        body.text,
        user_id=body.user_id,
        file_id=body.file_id,
        file_name=body.file_name,
    )
    return IndexResponse(file_id=result.file_id, chunk_count=result.chunk_count)


# --------------------------------------------------------------------------- #
_ERRORS = {
    400: {"model": ErrorResponse},
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse, "description": "File belongs to another user"},
    404: {"model": ErrorResponse, "description": "File does not exist / not indexed"},
    422: {"model": ErrorResponse, "description": "Insufficient source context"},
    429: {"model": ErrorResponse, "description": "AI provider rate limited; retry with backoff"},
    502: {"model": ErrorResponse},
    503: {"model": ErrorResponse},
}


@protected.post("/plans/generate", response_model=StudyPlan, responses=_ERRORS)
async def generate_plan(body: StudyPlanRequest, services: Services = Depends(get_services)):
    """Grounded study plan. Persist on the backend side with
    ``StudyPlan.model_validate(response_json).to_dynamodb_item()`` (Decimal values cannot
    travel over JSON, so the DynamoDB item is built where it is written)."""
    return await services.plans.generate(body)


@protected.post("/quizzes/generate", response_model=Quiz, responses=_ERRORS)
async def generate_quiz(body: QuizRequest, services: Services = Depends(get_services)):
    """Grounded quiz. ``topic`` omitted/empty -> broad quiz across the selected files."""
    return await services.quizzes.generate(body)
