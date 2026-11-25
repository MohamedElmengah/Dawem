"""Pydantic contracts for the Dawem AI backend.

Three layers, on purpose:

1. **Requests**  - what the team's backend sends us (``StudyPlanRequest``, ``QuizRequest``).
2. **Drafts**    - the *only* models the LLM is asked to produce (``*Draft``). They
                   reference sources by ``chunk_id`` and never invent ids, user ids
                   or timestamps. They carry no defaults (structured-output friendly)
                   and tolerate unknown keys (``extra="ignore"``).
3. **Finals**    - what we persist / return (``StudyPlan``, ``Quiz``). Strict
                   (``extra="forbid"``), fully cited, and assembled by the pipeline
                   from a validated draft plus the retrieved chunks.

Why drafts reference ``source_chunk_ids`` instead of full citations: the pipeline
checks every id against the chunks it actually retrieved, so the model *cannot*
cite a source that was not in its context. Citation metadata (file name, page)
is then filled in from our own records, never from model output.

Budget / shape rules (days covered, per-day minutes <= budget) are enforced at
both the draft stage (via validation context, so the provider's repair loop can
feed the error back to the model) and the final stage (unconditionally, before
persistence).
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Any, Self, Sequence

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from dawem_ai.exceptions import INSUFFICIENT_CONTEXT_DETAIL

__all__ = [
    "TaskKind",
    "Difficulty",
    "ErrorCode",
    "StudyPlanRequest",
    "QuizRequest",
    "SourceCitation",
    "DailyTaskDraft",
    "StudyPlanDraft",
    "MCQDraft",
    "RecallQuestionDraft",
    "QuizDraft",
    "DailyTask",
    "StudyPlan",
    "MCQ",
    "ActiveRecallQuestion",
    "Quiz",
    "ErrorResponse",
]


# --------------------------------------------------------------------------- #
# Base configs & helpers
# --------------------------------------------------------------------------- #
class _Strict(BaseModel):
    """Requests and persisted models: reject anything unexpected."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class _LLMOutput(BaseModel):
    """Models sent to the LLM as a response schema.

    ``extra="ignore"`` keeps the generated JSON Schema free of
    ``additionalProperties: false``, which some providers reject.
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


def _new_id() -> str:
    return uuid.uuid4().hex


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _preview(values: Sequence[int], limit: int = 10) -> str:
    shown = ", ".join(str(v) for v in list(values)[:limit])
    return shown + (", ..." if len(values) > limit else "")


def _check_plan_shape(
    tasks: Sequence[Any], *, duration_days: int, daily_minutes: int
) -> None:
    """Every day 1..duration_days has >=1 task and no day exceeds the budget.

    Works on any task-like object exposing ``day_number`` and ``duration_minutes``.
    Error messages are written for the LLM: they are fed back verbatim by the
    provider's repair loop, so they must say exactly what to fix.
    """
    totals: dict[int, int] = {}
    for task in tasks:
        if task.day_number > duration_days:
            raise ValueError(
                f"A task has day_number {task.day_number} but the plan only "
                f"spans days 1..{duration_days}."
            )
        totals[task.day_number] = totals.get(task.day_number, 0) + task.duration_minutes

    missing = [d for d in range(1, duration_days + 1) if d not in totals]
    if missing:
        raise ValueError(
            f"Every day from 1 to {duration_days} needs at least one task. "
            f"Days with no task: {_preview(missing)}."
        )

    over = sorted(d for d, minutes in totals.items() if minutes > daily_minutes)
    if over:
        details = "; ".join(f"day {d} totals {totals[d]} min" for d in over[:10])
        raise ValueError(
            f"Daily time budget is {daily_minutes} minutes but: {details}. "
            f"Reduce task durations or move tasks to another day."
        )


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #
class TaskKind(StrEnum):
    READ = "read"
    PRACTICE = "practice"
    REVIEW = "review"
    QUIZ = "quiz"


class Difficulty(StrEnum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class ErrorCode(StrEnum):
    INSUFFICIENT_CONTEXT = "insufficient_context"  # HTTP 422
    FILE_NOT_FOUND = "file_not_found"  # HTTP 404 (file/object does not exist)
    FORBIDDEN = "forbidden"  # HTTP 403 (file exists but is owned by another user)
    RATE_LIMITED = "rate_limited"  # HTTP 429 (every AI provider hit its rate limit)
    INVALID_MODEL_OUTPUT = "invalid_model_output"  # HTTP 502
    PROVIDER_UNAVAILABLE = "provider_unavailable"  # HTTP 503
    # API-layer codes (added with dawem_ai.api); 422 stays reserved for insufficient_context.
    INVALID_REQUEST = "invalid_request"  # HTTP 400 (malformed body / fields)
    INVALID_FILE = "invalid_file"  # HTTP 400 / 413 / 415 (unreadable, too large, wrong type)
    INDEXING_FAILED = "indexing_failed"  # HTTP 503 (embedding/store/S3 read kept failing)
    UNAUTHORIZED = "unauthorized"  # HTTP 401 (missing/wrong X-API-Key)
    INTERNAL_ERROR = "internal_error"  # HTTP 500 (unexpected; no details leaked)


# --------------------------------------------------------------------------- #
# 1. Requests
# --------------------------------------------------------------------------- #
class StudyPlanRequest(_Strict):
    user_id: str = Field(min_length=1, max_length=128)
    file_ids: list[str] = Field(min_length=1, max_length=20)
    goal: str = Field(min_length=3, max_length=500)
    duration_days: int = Field(ge=1, le=365)
    daily_minutes: int = Field(ge=10, le=720)
    # FR-02 (Goal Creation): "level" and "optional context". The PDF does not enumerate
    # level values, so it is a free string (e.g. "beginner"); the backend owns the vocabulary.
    level: str = Field(min_length=1, max_length=50)
    context: str | None = Field(default=None, max_length=2000)

    @field_validator("level")
    @classmethod
    def _strip_level(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("level must not be blank")
        return v

    @field_validator("context")
    @classmethod
    def _blank_context_is_none(cls, v: str | None) -> str | None:
        return (v.strip() or None) if v is not None else None

    @field_validator("file_ids")
    @classmethod
    def _unique_file_ids(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("file_ids must be unique")
        return v


class QuizRequest(_Strict):
    user_id: str = Field(min_length=1, max_length=128)
    file_ids: list[str] = Field(min_length=1, max_length=20)
    topic: str | None = Field(default=None, max_length=300)
    num_mcqs: int = Field(default=10, ge=1, le=30)
    num_recall: int = Field(default=3, ge=0, le=10)

    @field_validator("file_ids")
    @classmethod
    def _unique_file_ids(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("file_ids must be unique")
        return v


# --------------------------------------------------------------------------- #
# Source citation (preserved end-to-end so the Flutter UI can render sources)
# --------------------------------------------------------------------------- #
class SourceCitation(_Strict):
    chunk_id: str = Field(min_length=1, max_length=128)
    file_id: str = Field(min_length=1, max_length=128)
    file_name: str = Field(min_length=1, max_length=255)
    page_number: int | None = Field(default=None, ge=1)  # None for non-paginated notes
    snippet: str | None = Field(default=None, max_length=500)
    score: float | None = Field(default=None, ge=0.0, le=1.0)  # normalized relevance


# --------------------------------------------------------------------------- #
# 2a. Study plan - LLM draft
# --------------------------------------------------------------------------- #
class DailyTaskDraft(_LLMOutput):
    day_number: int = Field(ge=1, description="1-based day of the plan.")
    title: str = Field(min_length=3, max_length=120)
    description: str = Field(min_length=1, max_length=1000)
    kind: TaskKind
    duration_minutes: int = Field(ge=5, le=720)
    source_chunk_ids: list[str] = Field(
        min_length=1,
        description="chunk_ids of the provided context this task is based on.",
    )


class StudyPlanDraft(_LLMOutput):
    title: str = Field(min_length=3, max_length=150)
    tasks: list[DailyTaskDraft] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_against_request(self, info: ValidationInfo) -> Self:
        # The request's limits arrive via validation context, e.g.
        #   schema.model_validate_json(raw, context={"duration_days": 7, "daily_minutes": 45})
        # Without context (e.g. when only the JSON Schema is generated) this is a no-op.
        ctx = info.context or {}
        duration_days = ctx.get("duration_days")
        daily_minutes = ctx.get("daily_minutes")
        if duration_days is not None and daily_minutes is not None:
            _check_plan_shape(
                self.tasks, duration_days=duration_days, daily_minutes=daily_minutes
            )
        return self


# --------------------------------------------------------------------------- #
# 2b. Study plan - final, persisted
# --------------------------------------------------------------------------- #
class DailyTask(_Strict):
    task_id: str = Field(default_factory=_new_id)
    day_number: int = Field(ge=1)
    title: str = Field(min_length=3, max_length=120)
    description: str = Field(min_length=1, max_length=1000)
    kind: TaskKind
    duration_minutes: int = Field(ge=5, le=720)
    sources: list[SourceCitation] = Field(min_length=1)  # zero-hallucination: no source, no task


class StudyPlan(_Strict):
    plan_id: str = Field(default_factory=_new_id)
    user_id: str = Field(min_length=1, max_length=128)
    file_ids: list[str] = Field(min_length=1)
    goal: str = Field(min_length=3, max_length=500)
    title: str = Field(min_length=3, max_length=150)
    duration_days: int = Field(ge=1, le=365)
    daily_minutes: int = Field(ge=10, le=720)
    tasks: list[DailyTask] = Field(min_length=1)
    created_at: datetime = Field(default_factory=_utcnow)

    @model_validator(mode="after")
    def _validate_plan(self) -> Self:
        ids = [t.task_id for t in self.tasks]
        if len(set(ids)) != len(ids):
            raise ValueError("task_id values must be unique within a plan")
        _check_plan_shape(
            self.tasks,
            duration_days=self.duration_days,
            daily_minutes=self.daily_minutes,
        )
        return self

    def to_dynamodb_item(self) -> dict[str, Any]:
        """JSON-safe dict for boto3's DynamoDB *resource* API.

        boto3 rejects Python ``float`` (our ``score`` fields), so floats become
        ``Decimal``; datetimes become ISO-8601 strings.

        WARNING: this is the whole plan in ONE item. DynamoDB items are capped at
        400 KB and a long plan (up to 365 days, every task with cited snippets) can
        exceed that. Persist the tasks with ``to_task_items`` (one item per
        ``DailyTask``, as in the project documentation's data model) and keep this
        for small plans or plan metadata only.

        TODO(schema): ``pk``/``sk`` below are PLACEHOLDERS. The documentation does not
        define the key design; replace with the team's real DynamoDB table definition.
        """
        item = json.loads(self.model_dump_json(), parse_float=Decimal)
        item["pk"] = f"USER#{self.user_id}"
        item["sk"] = f"PLAN#{self.plan_id}"
        return item

    def to_task_items(
        self, goal_id: str, start_date: date, *, status: str = "pending"
    ) -> list[dict[str, Any]]:
        """One DynamoDB item per ``DailyTask``, shaped like the documentation's
        ``DailyTask`` entity (section 9):

            taskId, goalId, userId, dayNumber, date, title, description,
            durationMinutes, status, sourceRefs

        * ``date`` = ``start_date + (day_number - 1)`` as ``YYYY-MM-DD``; ``start_date``
          is the goal's start date (the AI service does not know it).
        * ``status`` defaults to ``"pending"``; the documentation does not enumerate
          status values, so pass the backend's own initial value if it differs.
        * ``sourceRefs`` carries the citations (fileId, chunkId, fileName, pageNumber,
          snippet, score); ``None`` values are omitted and ``score`` is a ``Decimal``.
        * ``kind`` (read / practice / review / quiz) is the one attribute beyond the
          documented list; it is how FR-12 tells study tasks, reviews and quizzes apart.
          Drop it here if the backend does not want it.

        Each item is far below the 400 KB limit. Write them with ``batch_writer()``
        (25 items per request is handled by boto3).

        TODO(schema): ``pk``/``sk`` are PLACEHOLDERS (the documentation does not define the
        key design); adjust to the team's table, e.g. add a GSI on (userId, date) for
        ``GET /tasks/today``.
        """
        if not goal_id or not goal_id.strip():
            raise ValueError("goal_id is required")
        if isinstance(start_date, datetime):  # datetime is a date subclass: keep the day only
            start_date = start_date.date()

        items: list[dict[str, Any]] = []
        for task in self.tasks:
            refs: list[dict[str, Any]] = []
            for src in task.sources:
                ref: dict[str, Any] = {
                    "fileId": src.file_id,
                    "chunkId": src.chunk_id,
                    "fileName": src.file_name,
                }
                if src.page_number is not None:
                    ref["pageNumber"] = src.page_number
                if src.snippet is not None:
                    ref["snippet"] = src.snippet
                if src.score is not None:
                    ref["score"] = Decimal(str(src.score))
                refs.append(ref)
            items.append(
                {
                    "pk": f"USER#{self.user_id}",
                    "sk": f"TASK#{task.task_id}",
                    "taskId": task.task_id,
                    "goalId": goal_id,
                    "userId": self.user_id,
                    "dayNumber": task.day_number,
                    "date": (start_date + timedelta(days=task.day_number - 1)).isoformat(),
                    "title": task.title,
                    "description": task.description,
                    "kind": task.kind.value,
                    "durationMinutes": task.duration_minutes,
                    "status": status,
                    "sourceRefs": refs,
                }
            )
        return items


# --------------------------------------------------------------------------- #
# 3a. Quiz - LLM draft
# --------------------------------------------------------------------------- #
def _check_options(options: Sequence[str], correct_index: int) -> None:
    if any(not o.strip() for o in options):
        raise ValueError("Options must be non-empty strings.")
    if len({o.strip().lower() for o in options}) != len(options):
        raise ValueError("Options must be distinct from each other.")
    if not 0 <= correct_index < len(options):
        raise ValueError(
            f"correct_option_index must be between 0 and {len(options) - 1}."
        )


class MCQDraft(_LLMOutput):
    question: str = Field(min_length=10, max_length=500)
    options: list[str] = Field(min_length=4, max_length=4)
    correct_option_index: int = Field(ge=0, le=3, description="0-based index into options.")
    explanation: str = Field(min_length=1, max_length=800)
    difficulty: Difficulty
    source_chunk_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_options(self) -> Self:
        _check_options(self.options, self.correct_option_index)
        return self


class RecallQuestionDraft(_LLMOutput):
    question: str = Field(min_length=10, max_length=500)
    model_answer: str = Field(min_length=1, max_length=1000)
    source_chunk_ids: list[str] = Field(min_length=1)


class QuizDraft(_LLMOutput):
    title: str = Field(min_length=3, max_length=150)
    mcqs: list[MCQDraft] = Field(min_length=1)
    recall_questions: list[RecallQuestionDraft]  # required; may be empty

    @model_validator(mode="after")
    def _check_counts(self, info: ValidationInfo) -> Self:
        ctx = info.context or {}
        num_mcqs, num_recall = ctx.get("num_mcqs"), ctx.get("num_recall")
        if num_mcqs is not None and len(self.mcqs) != num_mcqs:
            raise ValueError(f"Return exactly {num_mcqs} mcqs, got {len(self.mcqs)}.")
        if num_recall is not None and len(self.recall_questions) != num_recall:
            raise ValueError(
                f"Return exactly {num_recall} recall_questions, "
                f"got {len(self.recall_questions)}."
            )
        return self


# --------------------------------------------------------------------------- #
# 3b. Quiz - final, persisted
# --------------------------------------------------------------------------- #
class MCQ(_Strict):
    question_id: str = Field(default_factory=_new_id)
    question: str = Field(min_length=10, max_length=500)
    options: list[str] = Field(min_length=4, max_length=4)
    correct_option_index: int = Field(ge=0, le=3)
    explanation: str = Field(min_length=1, max_length=800)
    difficulty: Difficulty
    sources: list[SourceCitation] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_options(self) -> Self:
        _check_options(self.options, self.correct_option_index)
        return self


class ActiveRecallQuestion(_Strict):
    question_id: str = Field(default_factory=_new_id)
    question: str = Field(min_length=10, max_length=500)
    model_answer: str = Field(min_length=1, max_length=1000)
    sources: list[SourceCitation] = Field(min_length=1)


class Quiz(_Strict):
    quiz_id: str = Field(default_factory=_new_id)
    user_id: str = Field(min_length=1, max_length=128)
    file_ids: list[str] = Field(min_length=1)
    title: str = Field(min_length=3, max_length=150)
    mcqs: list[MCQ] = Field(min_length=1)
    recall_questions: list[ActiveRecallQuestion] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utcnow)

    def to_dynamodb_item(self) -> dict[str, Any]:
        """JSON-safe dict for boto3's DynamoDB *resource* API (floats -> ``Decimal``).

        The documentation's data model has no Quiz entity, so this keeps the same
        attribute names as the API response (``Quiz`` JSON) and only adds the key.
        The item holds the correct answers: keep it server-side and strip
        ``correct_option_index`` / ``model_answer`` before sending a quiz to Flutter.

        TODO(schema): ``pk``/``sk`` are PLACEHOLDERS, as for ``StudyPlan``.
        """
        item = json.loads(self.model_dump_json(), parse_float=Decimal)
        item["pk"] = f"USER#{self.user_id}"
        item["sk"] = f"QUIZ#{self.quiz_id}"
        return item


# --------------------------------------------------------------------------- #
# Error response (HTTP body for non-2xx)
# --------------------------------------------------------------------------- #
class ErrorResponse(_Strict):
    code: ErrorCode
    detail: str
    request_id: str | None = None

    @classmethod
    def insufficient_context(cls, request_id: str | None = None) -> Self:
        """Body for HTTP 422 when retrieval finds no relevant chunks."""
        return cls(
            code=ErrorCode.INSUFFICIENT_CONTEXT,
            detail=INSUFFICIENT_CONTEXT_DETAIL,
            request_id=request_id,
        )
