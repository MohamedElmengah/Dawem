"""FastAPI application factory.

    uvicorn dawem_ai.api.app:app --host 0.0.0.0 --port 8000

Environment: see ``.env.example``. ``DAWEM_AI_API_KEY`` (if set) is required as the
``X-API-Key`` header on every endpoint except ``/health``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from dawem_ai.api.errors import REQUEST_ID_HEADER, register_exception_handlers
from dawem_ai.api.routes import protected, public
from dawem_ai.api.services import Services, build_services_from_env

__all__ = ["create_app", "app"]

logger = logging.getLogger(__name__)

_RID_CLEAN = re.compile(r"[^A-Za-z0-9._-]")


def create_app(
    services: Services | None = None,
    *,
    api_key: str | None = None,
    max_upload_bytes: int | None = None,
    allowed_s3_buckets: set[str] | None = None,
) -> FastAPI:
    """Build the app. Pass ``services`` to inject fakes (tests); otherwise they are built
    from the environment at startup, so a missing API key fails the boot, not a request.

    When no services are injected (the real server) a local ``.env`` file is loaded first;
    variables already set in the environment win, so production config is never overridden.
    Tests inject ``services`` and therefore never read a developer's ``.env``."""
    if services is None:
        try:
            from dotenv import load_dotenv

            load_dotenv()
        except ImportError:  # pragma: no cover - python-dotenv is in requirements.txt
            logger.warning("python-dotenv not installed: .env is not loaded")

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        if application.state.services is None:
            application.state.services = await asyncio.to_thread(build_services_from_env)
        yield

    application = FastAPI(title="Dawem AI", version="1.0.0", lifespan=lifespan)
    application.state.services = services
    application.state.api_key = (
        os.environ.get("DAWEM_AI_API_KEY", "") if api_key is None else api_key
    )
    application.state.max_upload_bytes = (
        int(float(os.environ.get("MAX_UPLOAD_MB", "20")) * 1024 * 1024)
        if max_upload_bytes is None
        else max_upload_bytes
    )
    # Optional allow-list for POST /files/index-s3 (comma-separated env var). Empty = any
    # bucket the service's IAM role can read; set it in production as defence in depth.
    application.state.allowed_s3_buckets = (
        {b.strip() for b in os.environ.get("DAWEM_S3_ALLOWED_BUCKETS", "").split(",") if b.strip()}
        if allowed_s3_buckets is None
        else set(allowed_s3_buckets)
    )
    if not application.state.api_key:
        logger.warning("DAWEM_AI_API_KEY is not set: the API accepts unauthenticated calls")

    @application.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        rid = _RID_CLEAN.sub("", request.headers.get(REQUEST_ID_HEADER, ""))[:64]
        request.state.request_id = rid or uuid.uuid4().hex
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request.state.request_id
        return response

    register_exception_handlers(application)
    application.include_router(public)
    application.include_router(protected)
    return application


app = create_app()
