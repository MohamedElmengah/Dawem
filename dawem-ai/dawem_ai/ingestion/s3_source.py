"""Fetch a learning file from S3 so it can be indexed (``POST /files/index-s3``).

The project documentation uploads files to a private S3 bucket through a presigned URL
and triggers indexing from the S3 object-created event. The backend may either download
the object itself and call the multipart ``/files/index`` or hand us the bucket and key;
this module supports the second way.

``boto3`` is imported lazily, so the rest of the service (and the tests) never need it.
Credentials come from the standard AWS chain (an IAM role in production). Grant that role
``s3:GetObject`` on the learning-files bucket only (least privilege, documentation s.16).

Nothing here logs bucket names, keys or content, and error messages stay generic.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Protocol

from dawem_ai.exceptions import SourceObjectNotFoundError, SourceStorageError

__all__ = ["S3Fetcher", "Boto3S3Fetcher"]

logger = logging.getLogger(__name__)

_NOT_FOUND_CODES = {"404", "NoSuchKey", "NotFound", "NoSuchBucket"}


class S3Fetcher(Protocol):
    def size(self, *, bucket: str, key: str) -> int:
        """Object size in bytes (a HEAD request, no download)."""

    def download(self, *, bucket: str, key: str, dest: Path) -> None:
        """Write the object to ``dest``."""


class Boto3S3Fetcher:
    def __init__(self, client: Any | None = None) -> None:
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            try:
                import boto3
            except ImportError as exc:  # pragma: no cover
                raise ImportError("boto3 is not installed. Run: pip install boto3") from exc
            self._client = boto3.client("s3")
        return self._client

    def size(self, *, bucket: str, key: str) -> int:
        try:
            head = self.client.head_object(Bucket=bucket, Key=key)
        except Exception as exc:
            raise self._map_error(exc) from exc
        return int(head["ContentLength"])

    def download(self, *, bucket: str, key: str, dest: Path) -> None:
        try:
            self.client.download_file(bucket, key, str(dest))
        except Exception as exc:
            raise self._map_error(exc) from exc

    @staticmethod
    def _map_error(exc: Exception) -> Exception:
        """Missing object -> SourceObjectNotFoundError; any other AWS error ->
        SourceStorageError (HTTP 503). Non-AWS exceptions are bugs and pass through."""
        try:
            from botocore.exceptions import BotoCoreError, ClientError
        except ImportError:  # pragma: no cover
            return exc
        if isinstance(exc, ClientError):
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in _NOT_FOUND_CODES:
                return SourceObjectNotFoundError("S3 object not found")
            logger.warning("s3 read failed (ClientError %s)", code or "unknown")
            return SourceStorageError("S3 read failed")
        if isinstance(exc, BotoCoreError):
            logger.warning("s3 read failed (%s)", type(exc).__name__)
            return SourceStorageError("S3 read failed")
        return exc
