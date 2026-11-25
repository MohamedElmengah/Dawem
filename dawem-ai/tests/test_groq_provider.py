"""Offline tests for GroqProvider: a fake client stands in for the Groq SDK."""

import asyncio
import json
import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from dawem_ai.exceptions import (
    ProviderError,
    ProviderRateLimitError,
    ProviderUnavailableError,
)
from dawem_ai.llm import provider as provider_module
from dawem_ai.llm.provider import (
    DEFAULT_GROQ_MODEL,
    AiProvider,
    FallbackProvider,
    GroqProvider,
)
from dawem_ai.schemas.contracts import StudyPlanDraft

CTX = {"duration_days": 2, "daily_minutes": 60}


# ---- fake Groq SDK surface (only what GroqProvider touches) ---------------- #
class FakeAPIStatusError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"status {status_code}")
        self.status_code = status_code


class FakeAPIConnectionError(Exception):
    pass


class FakeAsyncGroq:
    instances: list["FakeAsyncGroq"] = []

    def __init__(self, **kwargs) -> None:
        self.init_kwargs = kwargs
        FakeAsyncGroq.instances.append(self)


FAKE_SDK = SimpleNamespace(
    APIStatusError=FakeAPIStatusError,
    APIConnectionError=FakeAPIConnectionError,
    AsyncGroq=FakeAsyncGroq,
)


class FakeClient:
    """Mimics `client.chat.completions.create` and records every call."""

    def __init__(self, script) -> None:
        self.script = list(script)
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=item))])


def _groq(script, **kwargs) -> tuple[GroqProvider, FakeClient]:
    client = FakeClient(script)
    return GroqProvider(client=client, backoff_base_s=0, **kwargs), client


def _call(provider, **overrides):
    args = dict(prompt="Plan my study", schema=StudyPlanDraft, system_prompt="You are Dawem.", temperature=0.3)
    args.update(overrides)
    return asyncio.run(provider._generate_json(**args))


def _task(day, minutes):
    return {"day_number": day, "title": f"Day {day} study", "description": "Read it.",
            "kind": "read", "duration_minutes": minutes, "source_chunk_ids": ["c1"]}


# ---- request shape ---------------------------------------------------------- #
def test_default_model_is_llama_33_70b_versatile():
    provider, _ = _groq([])
    assert DEFAULT_GROQ_MODEL == "llama-3.3-70b-versatile" == provider.model


def test_request_uses_json_mode_and_embeds_schema_in_system_prompt():
    provider, client = _groq(['{"ok": true}'])
    assert _call(provider) == '{"ok": true}'

    (call,) = client.calls
    assert call["model"] == "llama-3.3-70b-versatile"
    assert call["response_format"] == {"type": "json_object"}
    assert call["temperature"] == 0.3
    system, user = call["messages"]
    assert system["role"] == "system" and user == {"role": "user", "content": "Plan my study"}
    assert system["content"].startswith("You are Dawem.")
    assert "JSON" in system["content"]  # Groq JSON mode requires the word "JSON" in the messages
    schema_text = system["content"].split("JSON Schema:\n", 1)[1]
    assert json.loads(schema_text) == StudyPlanDraft.model_json_schema()


def test_empty_model_content_returns_empty_string_for_the_repair_loop():
    provider, _ = _groq([None])
    assert _call(provider) == ""


# ---- error mapping ---------------------------------------------------------- #
def test_http_and_connection_errors_map_to_retryable_or_fatal_provider_errors():
    cases = [
        (FakeAPIStatusError(429), ProviderRateLimitError),
        (FakeAPIStatusError(500), ProviderUnavailableError),
        (FakeAPIStatusError(503), ProviderUnavailableError),
        (FakeAPIStatusError(408), ProviderUnavailableError),
        (FakeAPIConnectionError("dns"), ProviderUnavailableError),
    ]
    with patch.object(provider_module, "groq_sdk", FAKE_SDK):
        for sdk_error, expected in cases:
            provider, _ = _groq([sdk_error])
            with pytest.raises(expected):
                _call(provider)

        for code in (400, 401, 403, 404):  # retrying cannot help: plain ProviderError
            provider, _ = _groq([FakeAPIStatusError(code)])
            with pytest.raises(ProviderError) as info:
                _call(provider)
            assert type(info.value) is ProviderError


def test_non_sdk_exceptions_are_not_disguised_as_provider_errors():
    with patch.object(provider_module, "groq_sdk", FAKE_SDK):
        provider, _ = _groq([KeyError("bug in our code")])
        with pytest.raises(KeyError):
            _call(provider)


def test_rate_limit_is_retried_by_our_layer_then_succeeds():
    good = json.dumps({"title": "My plan", "tasks": [_task(1, 30), _task(2, 30)]})
    with patch.object(provider_module, "groq_sdk", FAKE_SDK):
        provider, client = _groq([FakeAPIStatusError(429), good], max_retries=3)
        plan = asyncio.run(
            provider.generate_structured(prompt="p", schema=StudyPlanDraft, validation_context=CTX)
        )
    assert len(plan.tasks) == 2 and len(client.calls) == 2


# ---- construction ----------------------------------------------------------- #
def test_sdk_client_is_built_without_sdk_level_retries():
    FakeAsyncGroq.instances.clear()
    with patch.object(provider_module, "groq_sdk", FAKE_SDK):
        GroqProvider(api_key="gsk_test", request_timeout_s=12)
    (sdk_client,) = FakeAsyncGroq.instances
    assert sdk_client.init_kwargs == {"api_key": "gsk_test", "max_retries": 0, "timeout": 12}


def test_requires_an_api_key_when_no_client_is_injected():
    with patch.object(provider_module, "groq_sdk", FAKE_SDK):
        with pytest.raises(ValueError):
            GroqProvider(api_key="")


def test_from_env_reads_key_model_and_resilience_settings():
    env = {"GROQ_API_KEY": "gsk_test", "GROQ_MODEL": "llama-3.1-8b-instant",
           "LLM_MAX_RETRIES": "5", "LLM_MAX_REPAIR_ATTEMPTS": "1", "LLM_REQUEST_TIMEOUT_S": "30"}
    with patch.object(provider_module, "groq_sdk", FAKE_SDK), patch.dict(os.environ, env, clear=True):
        provider = GroqProvider.from_env()
        assert (provider.model, provider.max_retries, provider.max_repair_attempts,
                provider.request_timeout_s) == ("llama-3.1-8b-instant", 5, 1, 30.0)
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(ValueError):
            GroqProvider.from_env()


# ---- FallbackProvider([gemini, groq]) --------------------------------------- #
class ExhaustedPrimary(AiProvider):
    name = "gemini-like"

    def __init__(self):
        super().__init__(max_retries=1, backoff_base_s=0)

    async def _generate_json(self, **_):
        raise ProviderRateLimitError("free-tier quota exhausted")


def test_fallback_uses_groq_when_the_primary_is_rate_limited():
    good = json.dumps({"title": "My plan", "tasks": [_task(1, 30), _task(2, 30)]})
    groq, client = _groq([good])
    plan = asyncio.run(
        FallbackProvider([ExhaustedPrimary(), groq]).generate_structured(
            prompt="p", schema=StudyPlanDraft, validation_context=CTX
        )
    )
    assert plan.title == "My plan" and len(client.calls) == 1
