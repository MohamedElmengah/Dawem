"""Offline API tests: status codes, error bodies, auth, upload handling (no network)."""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("multipart")  # python-multipart, needed for /files/index uploads

from datetime import date  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from dawem_ai.api.app import create_app  # noqa: E402
from dawem_ai.api.services import Services  # noqa: E402
from dawem_ai.exceptions import (  # noqa: E402
    INSUFFICIENT_CONTEXT_DETAIL,
    ProviderRateLimitError,
    ProviderUnavailableError,
    SourceObjectNotFoundError,
    SourceStorageError,
    StructuredOutputError,
)
from dawem_ai.ingestion import IndexingService  # noqa: E402
from dawem_ai.pipelines import PlanPipeline, QuizPipeline  # noqa: E402
from dawem_ai.retrieval import Retriever  # noqa: E402
from dawem_ai.schemas.contracts import Quiz, StudyPlan  # noqa: E402
from tests.fake_llm import FakeLLM, RaisingLLM  # noqa: E402
from tests.fakes import FakeEmbedder, InMemoryChunkStore  # noqa: E402

BIO = (
    "Photosynthesis converts sunlight, water and carbon dioxide into glucose and oxygen "
    "inside chloroplasts. The light reactions happen in the thylakoid membranes. "
    "The Calvin cycle fixes carbon dioxide into sugar in the stroma."
)
GOAL = "photosynthesis converts sunlight water carbon dioxide into glucose oxygen chloroplasts"


class FakeS3:
    """In-memory S3Fetcher: ``objects`` maps (bucket, key) -> bytes."""

    def __init__(self, objects=None, *, error=None):
        self.objects = objects or {}
        self.error = error
        self.downloads = 0

    def size(self, *, bucket, key):
        if self.error:
            raise self.error
        try:
            return len(self.objects[(bucket, key)])
        except KeyError:
            raise SourceObjectNotFoundError("S3 object not found") from None

    def download(self, *, bucket, key, dest):
        self.downloads += 1
        dest.write_bytes(self.objects[(bucket, key)])


def make_client(llm=None, *, api_key="", max_upload_bytes=1_000_000, s3=None,
                allowed_s3_buckets=None, **client_kw):
    store, embedder = InMemoryChunkStore(), FakeEmbedder()
    indexer = IndexingService(store, embedder, chunk_size=200, chunk_overlap=40, sleep=lambda s: None)
    retriever = Retriever(store, embedder, top_k=8, relevance_threshold=0.35)
    llm = llm or FakeLLM()
    services = Services(
        indexer=indexer, plans=PlanPipeline(retriever, llm), quizzes=QuizPipeline(retriever, llm), s3=s3
    )
    app = create_app(
        services, api_key=api_key, max_upload_bytes=max_upload_bytes,
        allowed_s3_buckets=allowed_s3_buckets,
    )
    return TestClient(app, **client_kw), llm


def index_bio(client, user="u1", file_id="f1", headers=None):
    return client.post(
        "/files/index-notes",
        json={"user_id": user, "file_id": file_id, "file_name": "Biology.md", "text": BIO},
        headers=headers,
    )


def plan_body(**over):
    return {
        "user_id": "u1", "file_ids": ["f1"], "goal": GOAL, "duration_days": 10,
        "daily_minutes": 60, "level": "beginner", **over,
    }


# ------------------------------------------------------------------ health ---- #
def test_health_is_ok_and_needs_no_key():
    client, _ = make_client(api_key="s3cret")
    r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


# --------------------------------------------------------------- happy paths --- #
def test_index_then_generate_plan_and_quiz_return_valid_contracts():
    client, llm = make_client()
    r = index_bio(client)
    assert r.status_code == 200 and r.json()["file_id"] == "f1" and r.json()["chunk_count"] >= 1

    r = client.post("/plans/generate", json=plan_body(duration_days=10))
    assert r.status_code == 200
    plan = StudyPlan.model_validate(r.json())
    assert len(plan.tasks) == 20 and llm.calls == 2  # 7 + 3 days
    assert all(s.file_name == "Biology.md" for t in plan.tasks for s in t.sources)
    assert plan.to_dynamodb_item()["pk"] == "USER#u1"  # DynamoDB-ready on the receiving side
    items = plan.to_task_items("goal-1", date(2026, 9, 1))  # one DynamoDB item per DailyTask
    assert len(items) == 20 and {i["goalId"] for i in items} == {"goal-1"}

    r = client.post(
        "/quizzes/generate",
        json={"user_id": "u1", "file_ids": ["f1"], "num_mcqs": 4, "num_recall": 1},  # topic omitted
    )
    assert r.status_code == 200
    quiz = Quiz.model_validate(r.json())
    assert len(quiz.mcqs) == 4 and len(quiz.recall_questions) == 1


def test_file_upload_indexes_txt_and_uses_the_original_name_in_citations():
    client, _ = make_client()
    r = client.post(
        "/files/index",
        data={"user_id": "u1", "file_id": "f1"},
        files={"file": ("../weird/notes.txt", BIO.encode(), "text/plain")},
    )
    assert r.status_code == 200 and r.json()["chunk_count"] >= 1
    plan = StudyPlan.model_validate(client.post("/plans/generate", json=plan_body(duration_days=2)).json())
    assert {s.file_name for t in plan.tasks for s in t.sources} == {"notes.txt"}

    r = client.post(
        "/files/index",
        data={"user_id": "u1", "file_id": "f2", "file_name": "Lecture 1.md"},
        files={"file": ("x.md", BIO.encode(), "text/markdown")},
    )
    assert r.status_code == 200


# ------------------------------------------------------------- 422 grounding --- #
def test_insufficient_context_is_422_with_the_contract_body_and_request_id():
    client, llm = make_client()
    index_bio(client)
    r = client.post(
        "/plans/generate",
        json=plan_body(goal="quantum chromodynamics gluon plasma"),
        headers={"X-Request-ID": "req-123"},
    )
    assert r.status_code == 422
    assert r.json() == {
        "code": "insufficient_context",
        "detail": INSUFFICIENT_CONTEXT_DETAIL,
        "request_id": "req-123",
    }
    assert r.headers["X-Request-ID"] == "req-123"
    assert llm.calls == 0

    r = client.post("/quizzes/generate", json={"user_id": "u1", "file_ids": ["f1"], "topic": "medieval castles"})
    assert r.status_code == 422 and r.json()["code"] == "insufficient_context"


def test_422_is_only_ever_used_for_insufficient_context():
    client, _ = make_client()
    r = client.post("/plans/generate", json=plan_body(duration_days=0))
    assert r.status_code == 400 and r.json()["code"] == "invalid_request"


# ----------------------------------------------------------------- ownership --- #
def test_foreign_files_are_403_and_unknown_files_are_404():
    # Project documentation s.17: 403 = user does not own the resource, 404 = does not exist.
    client, llm = make_client()
    index_bio(client, user="u1", file_id="f1")
    foreign = client.post("/plans/generate", json=plan_body(user_id="u2"))
    unknown = client.post("/plans/generate", json=plan_body(file_ids=["nope"]))
    assert foreign.status_code == 403 and foreign.json()["code"] == "forbidden"
    assert unknown.status_code == 404 and unknown.json()["code"] == "file_not_found"
    assert client.post("/quizzes/generate", json={"user_id": "u2", "file_ids": ["f1"]}).status_code == 403
    assert client.post("/quizzes/generate", json={"user_id": "u1", "file_ids": ["nope"]}).status_code == 404
    assert llm.calls == 0


def test_indexing_a_file_id_owned_by_another_user_is_403():
    client, _ = make_client()
    index_bio(client, user="u1", file_id="f1")
    r = index_bio(client, user="u2", file_id="f1")
    assert r.status_code == 403 and r.json()["code"] == "forbidden"


# ------------------------------------------------------ DELETE /files/{id} ---- #
def test_delete_file_removes_the_index_and_is_then_404():
    client, llm = make_client()
    index_bio(client)
    r = client.delete("/files/f1", params={"user_id": "u1"})
    assert r.status_code == 204 and r.content == b""
    assert client.post("/plans/generate", json=plan_body()).status_code == 404
    again = client.delete("/files/f1", params={"user_id": "u1"})
    assert again.status_code == 404 and again.json()["code"] == "file_not_found"
    assert llm.calls == 0


def test_delete_file_of_another_user_is_403_and_deletes_nothing():
    client, _ = make_client()
    index_bio(client, user="u1", file_id="f1")
    r = client.delete("/files/f1", params={"user_id": "u2"})
    assert r.status_code == 403 and r.json()["code"] == "forbidden"
    assert client.post("/plans/generate", json=plan_body(duration_days=2)).status_code == 200


def test_delete_file_needs_user_id_and_the_api_key():
    client, _ = make_client(api_key="s3cret")
    h = {"X-API-Key": "s3cret"}
    assert client.delete("/files/f1", params={"user_id": "u1"}).status_code == 401
    r = client.delete("/files/f1", headers=h)  # user_id missing
    assert r.status_code == 400 and r.json()["code"] == "invalid_request"
    assert client.delete("/files/f1", params={"user_id": "u1"}, headers=h).status_code == 404


# ------------------------------------------------ POST /files/index-s3 -------- #
BUCKET, KEY = "learning-files", "uploads/u1/f1/Biology.md"


def s3_body(**over):
    return {"user_id": "u1", "file_id": "f1", "bucket": BUCKET, "s3_key": KEY, **over}


def test_index_s3_indexes_the_object_and_citations_use_the_key_name():
    s3 = FakeS3({(BUCKET, KEY): BIO.encode()})
    client, _ = make_client(s3=s3)
    r = client.post("/files/index-s3", json=s3_body())
    assert r.status_code == 200 and r.json()["file_id"] == "f1" and r.json()["chunk_count"] >= 1
    plan = StudyPlan.model_validate(client.post("/plans/generate", json=plan_body(duration_days=2)).json())
    assert {s.file_name for t in plan.tasks for s in t.sources} == {"Biology.md"}


def test_index_s3_file_name_overrides_the_key_name():
    s3 = FakeS3({(BUCKET, "uploads/9f8e7d.md"): BIO.encode()})
    client, _ = make_client(s3=s3)
    r = client.post("/files/index-s3", json=s3_body(s3_key="uploads/9f8e7d.md", file_name="Biology notes.md"))
    assert r.status_code == 200
    plan = StudyPlan.model_validate(client.post("/plans/generate", json=plan_body(duration_days=2)).json())
    assert {s.file_name for t in plan.tasks for s in t.sources} == {"Biology notes.md"}


def test_index_s3_error_mapping():
    # missing object -> 404
    s3 = FakeS3()
    client, _ = make_client(s3=s3)
    r = client.post("/files/index-s3", json=s3_body())
    assert r.status_code == 404 and r.json()["code"] == "file_not_found" and s3.downloads == 0

    # too large -> 413, rejected from the HEAD size before any download
    s3 = FakeS3({(BUCKET, KEY): b"x" * 200})
    client, _ = make_client(s3=s3, max_upload_bytes=50)
    r = client.post("/files/index-s3", json=s3_body())
    assert r.status_code == 413 and r.json()["code"] == "invalid_file" and s3.downloads == 0

    # unsupported extension -> 415 (checked before touching S3)
    s3 = FakeS3({(BUCKET, "a/virus.exe"): b"MZ"})
    client, _ = make_client(s3=s3)
    r = client.post("/files/index-s3", json=s3_body(s3_key="a/virus.exe"))
    assert r.status_code == 415 and r.json()["code"] == "invalid_file" and s3.downloads == 0

    # S3 unreadable -> 503 indexing_failed, without leaking details
    client, _ = make_client(s3=FakeS3(error=SourceStorageError("AccessDenied for arn:secret")))
    r = client.post("/files/index-s3", json=s3_body())
    assert r.status_code == 503 and r.json()["code"] == "indexing_failed" and "secret" not in r.text


def test_index_s3_for_a_file_id_owned_by_another_user_is_403():
    s3 = FakeS3({(BUCKET, KEY): BIO.encode()})
    client, _ = make_client(s3=s3)
    index_bio(client, user="u1", file_id="f1")
    r = client.post("/files/index-s3", json=s3_body(user_id="u2"))
    assert r.status_code == 403 and r.json()["code"] == "forbidden"


def test_index_s3_bucket_allow_list_and_request_validation():
    s3 = FakeS3({(BUCKET, KEY): BIO.encode(), ("other-bucket", KEY): BIO.encode()})
    client, _ = make_client(s3=s3, allowed_s3_buckets={BUCKET})
    r = client.post("/files/index-s3", json=s3_body(bucket="other-bucket"))
    assert r.status_code == 400 and r.json()["code"] == "invalid_request" and s3.downloads == 0
    assert client.post("/files/index-s3", json=s3_body()).status_code == 200

    for bad in ({"bucket": "Bad_Bucket!"}, {"s3_key": ""}, {"surprise": 1}):
        assert client.post("/files/index-s3", json=s3_body(**bad)).status_code == 400
    missing = {k: v for k, v in s3_body().items() if k != "user_id"}
    assert client.post("/files/index-s3", json=missing).status_code == 400


def test_index_s3_requires_the_api_key_and_a_configured_fetcher():
    s3 = FakeS3({(BUCKET, KEY): BIO.encode()})
    client, _ = make_client(api_key="s3cret", s3=s3)
    assert client.post("/files/index-s3", json=s3_body()).status_code == 401
    ok = client.post("/files/index-s3", json=s3_body(), headers={"X-API-Key": "s3cret"})
    assert ok.status_code == 200

    unwired, _ = make_client(raise_server_exceptions=False)  # no S3 fetcher -> wiring bug
    r = unwired.post("/files/index-s3", json=s3_body())
    assert r.status_code == 500 and r.json()["code"] == "internal_error"


# --------------------------------------------------- level / context (FR-02) --- #
def test_plan_request_requires_level_and_passes_level_and_context_to_the_model():
    client, llm = make_client()
    index_bio(client)
    no_level = {k: v for k, v in plan_body().items() if k != "level"}
    r = client.post("/plans/generate", json=no_level)
    assert r.status_code == 400 and r.json()["code"] == "invalid_request"
    assert client.post("/plans/generate", json=plan_body(level="  ")).status_code == 400

    r = client.post("/plans/generate", json=plan_body(duration_days=2, level="advanced", context="Evenings only"))
    assert r.status_code == 200
    assert "Learner level: advanced" in llm.prompts[0] and "Evenings only" in llm.prompts[0]


# -------------------------------------------------------- provider failures ---- #
@pytest.mark.parametrize(
    "exc, status, code",
    [
        (ProviderUnavailableError("down"), 503, "provider_unavailable"),
        (ProviderRateLimitError("down"), 429, "rate_limited"),  # documentation s.17: 429
        (StructuredOutputError("bad json"), 502, "invalid_model_output"),
    ],
)
def test_provider_failures_map_to_503_429_and_502(exc, status, code):
    client, _ = make_client(RaisingLLM(exc))
    index_bio(client)
    r = client.post("/plans/generate", json=plan_body())
    assert r.status_code == status and r.json()["code"] == code
    assert "down" not in r.text and "bad json" not in r.text


def test_unexpected_errors_are_500_without_leaking_details():
    client, _ = make_client(RaisingLLM(RuntimeError("boom with study text")), raise_server_exceptions=False)
    index_bio(client)
    r = client.post("/quizzes/generate", json={"user_id": "u1", "file_ids": ["f1"]})
    assert r.status_code == 500 and r.json()["code"] == "internal_error"
    assert "boom" not in r.text


# ------------------------------------------------------------- bad requests ---- #
def test_validation_errors_are_400_and_never_echo_the_input():
    client, _ = make_client()
    r = client.post("/plans/generate", json=plan_body(goal="My very private goal", duration_days=0))
    assert r.status_code == 400 and r.json()["code"] == "invalid_request"
    assert "private" not in r.text
    r = client.post("/plans/generate", json={**plan_body(), "surprise": 1})
    assert r.status_code == 400


def test_upload_rejections():
    client, _ = make_client(max_upload_bytes=50)
    bad_type = client.post(
        "/files/index", data={"user_id": "u1", "file_id": "f1"}, files={"file": ("a.exe", b"MZ", "application/octet-stream")}
    )
    assert bad_type.status_code == 415 and bad_type.json()["code"] == "invalid_file"

    empty = client.post(
        "/files/index", data={"user_id": "u1", "file_id": "f1"}, files={"file": ("a.txt", b"", "text/plain")}
    )
    assert empty.status_code == 400 and empty.json()["code"] == "invalid_file"

    big = client.post(
        "/files/index", data={"user_id": "u1", "file_id": "f1"}, files={"file": ("a.txt", b"x" * 200, "text/plain")}
    )
    assert big.status_code == 413 and big.json()["code"] == "invalid_file"


# --------------------------------------------------------------------- auth ---- #
def test_api_key_is_required_when_configured():
    client, _ = make_client(api_key="s3cret")
    assert index_bio(client).status_code == 401
    assert index_bio(client, headers={"X-API-Key": "wrong"}).status_code == 401
    r = index_bio(client, headers={"X-API-Key": "s3cret"})
    assert r.status_code == 200
    assert client.post("/plans/generate", json=plan_body()).json()["code"] == "unauthorized"
