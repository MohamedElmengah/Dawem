"""Offline tests: no network, no API keys. Run with `pytest` from dawem-ai/."""

import asyncio
import json
from datetime import date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from dawem_ai.exceptions import (
    ProviderRateLimitError,
    ProviderUnavailableError,
    StructuredOutputError,
)
from dawem_ai.llm.provider import AiProvider, FallbackProvider
from dawem_ai.schemas.contracts import (
    ActiveRecallQuestion,
    DailyTask,
    ErrorResponse,
    MCQ,
    Quiz,
    SourceCitation,
    StudyPlan,
    StudyPlanDraft,
    StudyPlanRequest,
)

CTX = {"duration_days": 2, "daily_minutes": 60}


def _task(day: int, minutes: int) -> dict:
    return {
        "day_number": day,
        "title": f"Day {day} study",
        "description": "Read and summarise the section.",
        "kind": "read",
        "duration_minutes": minutes,
        "source_chunk_ids": ["c1"],
    }


def _draft_json(*tasks: dict) -> str:
    return json.dumps({"title": "My plan", "tasks": list(tasks)})


# ----------------------------- contracts ----------------------------------- #
def test_draft_within_budget_passes():
    StudyPlanDraft.model_validate_json(_draft_json(_task(1, 30), _task(2, 60)), context=CTX)


def test_draft_over_budget_fails_with_actionable_message():
    with pytest.raises(ValidationError, match="day 1 totals 90 min"):
        StudyPlanDraft.model_validate_json(
            _draft_json(_task(1, 45), _task(1, 45), _task(2, 30)), context=CTX
        )


def test_draft_missing_day_fails():
    with pytest.raises(ValidationError, match="Days with no task: 2"):
        StudyPlanDraft.model_validate_json(_draft_json(_task(1, 30)), context=CTX)


def test_draft_without_context_skips_budget_check():
    StudyPlanDraft.model_validate_json(_draft_json(_task(1, 600)))


def _citation() -> SourceCitation:
    return SourceCitation(chunk_id="c1", file_id="f1", file_name="notes.pdf", page_number=3, score=0.8)


def test_final_plan_enforces_budget_before_persistence():
    tasks = [
        DailyTask(day_number=1, title="Too long", description="x", kind="read",
                  duration_minutes=90, sources=[_citation()]),
    ]
    with pytest.raises(ValidationError, match="Daily time budget"):
        StudyPlan(user_id="u1", file_ids=["f1"], goal="Pass exam", title="Plan",
                  duration_days=1, daily_minutes=60, tasks=tasks)


def test_final_plan_to_dynamodb_item_has_no_floats():
    plan = StudyPlan(
        user_id="u1", file_ids=["f1"], goal="Pass exam", title="Plan",
        duration_days=1, daily_minutes=60,
        tasks=[DailyTask(day_number=1, title="Read ch1", description="x", kind="read",
                         duration_minutes=45, sources=[_citation()])],
    )
    item = plan.to_dynamodb_item()
    assert not any(isinstance(v, float) for v in _walk(item))


def _three_day_plan() -> StudyPlan:
    """Days 1-3 (the plan validator requires every day to have a task)."""
    return StudyPlan(
        user_id="u1", file_ids=["f1"], goal="Pass exam", title="Plan",
        duration_days=3, daily_minutes=60,
        tasks=[
            DailyTask(day_number=1, title="Read ch1", description="x", kind="read",
                      duration_minutes=45, sources=[_citation()]),
            DailyTask(day_number=2, title="Practice ch1", description="z", kind="practice",
                      duration_minutes=40, sources=[_citation()]),
            DailyTask(day_number=3, title="Review ch1", description="y", kind="review",
                      duration_minutes=30,
                      sources=[SourceCitation(chunk_id="c2", file_id="f1", file_name="notes.md")]),
        ],
    )


# ------------- to_task_items: the documentation's DailyTask entity ------------- #
DOC_DAILY_TASK_ATTRIBUTES = {
    "taskId", "goalId", "userId", "dayNumber", "date", "title", "description",
    "durationMinutes", "status", "sourceRefs",
}


def test_to_task_items_is_one_item_per_task_with_the_documented_attributes():
    plan = _three_day_plan()
    items = plan.to_task_items("goal-7", date(2026, 9, 1))
    assert len(items) == 3
    for item, task in zip(items, plan.tasks):
        keys = set(item) - {"pk", "sk"}
        assert DOC_DAILY_TASK_ATTRIBUTES <= keys
        assert keys - DOC_DAILY_TASK_ATTRIBUTES == {"kind"}  # the one documented-extra attribute
        assert item["taskId"] == task.task_id and item["goalId"] == "goal-7"
        assert item["userId"] == "u1" and item["status"] == "pending"
        assert item["durationMinutes"] == task.duration_minutes
    assert len({i["sk"] for i in items}) == 3  # distinct sort keys


def test_to_task_items_dates_are_start_date_plus_day_offset():
    items = _three_day_plan().to_task_items("g", date(2026, 9, 29))
    assert [(i["dayNumber"], i["date"]) for i in items] == [
        (1, "2026-09-29"), (2, "2026-09-30"), (3, "2026-10-01"),
    ]
    # a datetime start keeps only the day
    again = _three_day_plan().to_task_items("g", datetime(2026, 9, 29, 23, 30))
    assert again[0]["date"] == "2026-09-29"


def test_to_task_items_source_refs_are_dynamodb_safe():
    items = _three_day_plan().to_task_items("g", date(2026, 9, 1))
    ref = items[0]["sourceRefs"][0]
    assert ref == {"fileId": "f1", "chunkId": "c1", "fileName": "notes.pdf",
                   "pageNumber": 3, "score": Decimal("0.8")}
    assert items[2]["sourceRefs"] == [{"fileId": "f1", "chunkId": "c2", "fileName": "notes.md"}]  # None omitted
    assert not any(isinstance(v, float) for item in items for v in _walk(item))
    assert not any(v is None for item in items for v in _walk(item))


def test_to_task_items_custom_status_and_goal_id_validation():
    items = _three_day_plan().to_task_items("g", date(2026, 9, 1), status="TODO")
    assert {i["status"] for i in items} == {"TODO"}
    for bad in ("", "   "):
        with pytest.raises(ValueError):
            _three_day_plan().to_task_items(bad, date(2026, 9, 1))


def test_a_365_day_plan_stays_far_below_the_400kb_item_limit_per_task():
    tasks = [
        DailyTask(day_number=d, title=f"Day {d}", description="d" * 900, kind="read",
                  duration_minutes=30,
                  sources=[SourceCitation(chunk_id=f"c{d}-{k}", file_id="f1", file_name="n.pdf",
                                          snippet="s" * 500, score=0.5) for k in range(8)])
        for d in range(1, 366)
    ]
    plan = StudyPlan(user_id="u1", file_ids=["f1"], goal="Year", title="Plan",
                     duration_days=365, daily_minutes=60, tasks=tasks)
    items = plan.to_task_items("g", date(2026, 1, 1))
    assert len(items) == 365
    assert max(len(json.dumps(i, default=str)) for i in items) < 20_000  # one task ~ 6 KB
    assert len(json.dumps(plan.to_dynamodb_item(), default=str)) > 400_000  # why the single item fails


# ------------------------------ Quiz item ---------------------------------- #
def test_quiz_to_dynamodb_item_has_keys_answers_and_no_floats():
    quiz = Quiz(
        user_id="u1", file_ids=["f1"], title="Quiz",
        mcqs=[MCQ(question="What is a stack?", options=["LIFO", "FIFO", "Heap", "Tree"],
                  correct_option_index=0, explanation="LIFO.", difficulty="easy",
                  sources=[_citation()])],
        recall_questions=[ActiveRecallQuestion(question="Explain what a stack is.",
                                               model_answer="A LIFO structure.",
                                               sources=[_citation()])],
    )
    item = quiz.to_dynamodb_item()
    assert item["pk"] == "USER#u1" and item["sk"] == f"QUIZ#{quiz.quiz_id}"
    assert item["mcqs"][0]["correct_option_index"] == 0  # answers stay server-side
    assert item["mcqs"][0]["sources"][0]["score"] == Decimal("0.8")
    assert not any(isinstance(v, float) for v in _walk(item))


# ------------------------- request: level + context ------------------------- #
def _req(**over):
    base = dict(user_id="u1", file_ids=["f1"], goal="Pass the exam", duration_days=7,
                daily_minutes=30, level="beginner")
    return StudyPlanRequest(**{**base, **over})


def test_request_requires_level_and_context_is_optional():
    assert _req().context is None
    assert _req(level="  advanced ", context="  evenings only  ").level == "advanced"
    assert _req(context="  evenings only  ").context == "evenings only"
    assert _req(context="   ").context is None
    base = dict(user_id="u1", file_ids=["f1"], goal="Pass the exam", duration_days=7, daily_minutes=30)
    with pytest.raises(ValidationError):
        StudyPlanRequest(**base)  # level is required
    for bad in ({"level": ""}, {"level": "   "}, {"level": "x" * 51}, {"context": "x" * 2001}):
        with pytest.raises(ValidationError):
            _req(**bad)


def _walk(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)
    else:
        yield obj


def test_mcq_rejects_duplicate_options():
    with pytest.raises(ValidationError, match="distinct"):
        MCQ(question="What is a stack?", options=["LIFO", "lifo", "FIFO", "Tree"],
            correct_option_index=0, explanation="x", difficulty="easy", sources=[_citation()])


def test_insufficient_context_error_body():
    body = ErrorResponse.insufficient_context("req-1").model_dump(mode="json")
    assert body == {"code": "insufficient_context", "detail": "Insufficient source context",
                    "request_id": "req-1"}


# ----------------------------- provider ------------------------------------ #
class ScriptedProvider(AiProvider):
    """Returns/raises scripted items in order; records the prompts it receives."""

    name = "scripted"

    def __init__(self, script, **kwargs):
        super().__init__(backoff_base_s=0, **kwargs)
        self.script = list(script)
        self.prompts: list[str] = []

    async def _generate_json(self, *, prompt, schema, system_prompt, temperature):
        self.prompts.append(prompt)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _generate(provider, **kwargs):
    return asyncio.run(
        provider.generate_structured(
            prompt="make a plan", schema=StudyPlanDraft, validation_context=CTX, **kwargs
        )
    )


def test_repair_loop_feeds_validation_errors_back():
    bad = _draft_json(_task(1, 90), _task(2, 30))
    good = _draft_json(_task(1, 30), _task(2, 30))
    provider = ScriptedProvider([bad, good])
    plan = _generate(provider)
    assert len(plan.tasks) == 2
    assert len(provider.prompts) == 2
    assert "day 1 totals 90 min" in provider.prompts[1]


def test_repair_loop_gives_up_with_structured_output_error():
    bad = _draft_json(_task(1, 90), _task(2, 30))
    provider = ScriptedProvider([bad, bad, bad], max_repair_attempts=2)
    with pytest.raises(StructuredOutputError):
        _generate(provider)


def test_transient_errors_are_retried():
    good = _draft_json(_task(1, 30), _task(2, 30))
    provider = ScriptedProvider([ProviderRateLimitError("429"), good], max_retries=3)
    assert len(_generate(provider).tasks) == 2


def test_fallback_moves_to_next_provider_when_first_is_exhausted():
    good = _draft_json(_task(1, 30), _task(2, 30))
    primary = ScriptedProvider([ProviderRateLimitError("429")], max_retries=1)
    secondary = ScriptedProvider([good])
    plan = _generate(FallbackProvider([primary, secondary]))
    assert len(plan.tasks) == 2
    assert secondary.prompts


def test_fallback_reports_429_only_when_every_provider_was_rate_limited():
    a = ScriptedProvider([ProviderRateLimitError("429")], max_retries=1)
    b = ScriptedProvider([ProviderRateLimitError("429")], max_retries=1)
    with pytest.raises(ProviderRateLimitError):
        _generate(FallbackProvider([a, b]))

    c = ScriptedProvider([ProviderRateLimitError("429")], max_retries=1)
    d = ScriptedProvider([ProviderUnavailableError("down")], max_retries=1)
    with pytest.raises(ProviderUnavailableError) as info:
        _generate(FallbackProvider([c, d]))
    assert not isinstance(info.value, ProviderRateLimitError)  # mixed failures = outage (503)
