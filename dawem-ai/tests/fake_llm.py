"""Offline LLM doubles for the pipeline / API tests.

``FakeLLM`` is a real ``AiProvider`` subclass, so the genuine JSON parsing, Pydantic
validation (with the pipeline's validation context) and repair loop run in the tests;
only the network call is replaced. The default responder reads the prompt the way a model
would (batch size, budget, context labels) and returns a valid, cited draft.
"""

from __future__ import annotations

import json
import re
from typing import Callable

from dawem_ai.llm.provider import AiProvider

Responder = Callable[[str, type], str]


def labels_in(prompt: str) -> list[str]:
    return re.findall(r"^\[(C\d+)\]", prompt, flags=re.MULTILINE)


def plan_json(prompt: str, *, per_task_minutes: int | None = None, cite=None) -> str:
    n_days = int(re.search(r"Number of days in this batch: (\d+)", prompt).group(1))
    budget = int(re.search(r"Daily time budget: (\d+) minutes", prompt).group(1))
    per = per_task_minutes if per_task_minutes is not None else max(5, budget // 2)
    labels = labels_in(prompt)
    tasks = []
    for day in range(1, n_days + 1):
        for k in range(2):
            ids = cite(day, k, labels) if cite else [labels[(day + k) % len(labels)]]
            tasks.append(
                {
                    "day_number": day,
                    "title": f"Batch day {day} task {k + 1}",
                    "description": "Study the cited material.",
                    "kind": "read" if k == 0 else "practice",
                    "duration_minutes": per,
                    "source_chunk_ids": ids,
                }
            )
    return json.dumps({"title": "Fake study plan", "tasks": tasks})


def quiz_json(prompt: str, *, cite=None) -> str:
    n_mcq = int(re.search(r"exactly (\d+) multiple-choice", prompt).group(1))
    n_rec = int(re.search(r"exactly (\d+) active-recall", prompt).group(1))
    labels = labels_in(prompt)

    def ids(i: int) -> list[str]:
        return cite(i, labels) if cite else [labels[i % len(labels)]]

    mcqs = [
        {
            "question": f"Question number {i} about the material?",
            "options": [f"Option A{i}", f"Option B{i}", f"Option C{i}", f"Option D{i}"],
            "correct_option_index": i % 4,
            "explanation": "Because the context says so.",
            "difficulty": ["easy", "medium", "hard"][i % 3],
            "source_chunk_ids": ids(i),
        }
        for i in range(n_mcq)
    ]
    recall = [
        {
            "question": f"Explain recall topic number {i} in your own words.",
            "model_answer": "A short model answer.",
            "source_chunk_ids": ids(i),
        }
        for i in range(n_rec)
    ]
    return json.dumps({"title": "Fake quiz", "mcqs": mcqs, "recall_questions": recall})


def default_responder(prompt: str, schema: type) -> str:
    return plan_json(prompt) if schema.__name__ == "StudyPlanDraft" else quiz_json(prompt)


class FakeLLM(AiProvider):
    name = "fake-llm"

    def __init__(self, responder: Responder = default_responder) -> None:
        super().__init__(max_retries=1, max_repair_attempts=2, backoff_base_s=0.0)
        self.responder = responder
        self.prompts: list[str] = []

    @property
    def calls(self) -> int:
        return len(self.prompts)

    async def _generate_json(self, *, prompt, schema, system_prompt, temperature) -> str:
        self.prompts.append(prompt)
        return self.responder(prompt, schema)


class RaisingLLM(AiProvider):
    """Fails every generation with ``exc`` (used for HTTP error-mapping tests)."""

    name = "raising-llm"

    def __init__(self, exc: Exception) -> None:
        super().__init__(max_retries=1, backoff_base_s=0.0)
        self.exc = exc

    async def generate_structured(self, **kwargs):  # type: ignore[override]
        raise self.exc

    async def _generate_json(self, **_):  # pragma: no cover
        raise self.exc
