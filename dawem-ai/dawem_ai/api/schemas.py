"""API-only request/response models (plan/quiz models come from dawem_ai.schemas)."""

from pydantic import BaseModel, ConfigDict, Field


class IndexResponse(BaseModel):
    file_id: str
    chunk_count: int


class IndexNotesRequest(BaseModel):
    """Notes typed or pasted in the app (no file upload)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    user_id: str = Field(min_length=1, max_length=128)
    file_id: str = Field(min_length=1, max_length=128)
    file_name: str = Field(min_length=1, max_length=255)
    text: str = Field(min_length=1, max_length=500_000)


class IndexS3Request(BaseModel):
    """Index a learning file that already sits in S3 (private bucket, read via IAM role)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    user_id: str = Field(min_length=1, max_length=128)
    file_id: str = Field(min_length=1, max_length=128)
    bucket: str = Field(min_length=3, max_length=63, pattern=r"^[a-z0-9][a-z0-9.\-]*[a-z0-9]$")
    s3_key: str = Field(min_length=1, max_length=1024)
    # LearningFile.filename in the documentation: what citations show instead of the key.
    file_name: str | None = Field(default=None, max_length=255)


class HealthResponse(BaseModel):
    status: str
