"""LLM provider abstraction.

Callers (pipelines, LangGraph nodes) depend only on ``AiProvider.generate_structured``.
Swapping Gemini for Groq or AWS Bedrock later means writing one new subclass that
implements ``_generate_json`` - nothing else changes.

Responsibilities are split so a new provider stays tiny:

* ``AiProvider`` (base)  - everything provider-agnostic: transient-error retries with
  exponential backoff + jitter, request timeouts, JSON parsing, Pydantic validation,
  and a *repair loop* that re-prompts the model with the exact validation errors.
* Subclass               - one method, ``_generate_json``: send the prompt, return the
  raw JSON text, and translate SDK exceptions into our ``Provider*Error`` types.
* ``FallbackProvider``   - tries providers in order (Gemini -> Groq -> ...).

Prompts contain the user's study material, so this module never logs prompt or
response bodies - only provider names, attempt numbers and error summaries.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
from abc import ABC, abstractmethod
from typing import Any, Mapping, Sequence, TypeVar

from pydantic import BaseModel, ValidationError

from dawem_ai.exceptions import (
    ProviderError,
    ProviderRateLimitError,
    ProviderUnavailableError,
    StructuredOutputError,
)

try:  # The SDK is only required when GeminiProvider is instantiated.
    from google import genai
    from google.genai import errors as genai_errors
    from google.genai import types as genai_types
except ImportError:  # pragma: no cover
    genai = None  # type: ignore[assignment]
    genai_errors = None  # type: ignore[assignment]
    genai_types = None  # type: ignore[assignment]

try:  # The SDK is only required when GroqProvider is instantiated without a client.
    import groq as groq_sdk
except ImportError:  # pragma: no cover
    groq_sdk = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# Free-tier friendly default. Model names change often: override with GEMINI_MODEL.
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL | re.IGNORECASE)


def _strip_code_fences(raw: str) -> str:
    """Some models wrap JSON in ```json fences even in JSON mode."""
    match = _FENCE_RE.match(raw)
    return match.group(1) if match else raw.strip()


def _format_validation_errors(exc: ValidationError, limit: int = 10) -> str:
    lines: list[str] = []
    for err in exc.errors(include_url=False, include_context=False, include_input=False)[
        :limit
    ]:
        loc = ".".join(str(part) for part in err["loc"]) or "(root)"
        lines.append(f"- {loc}: {err['msg']}")
    extra = exc.error_count() - limit
    if extra > 0:
        lines.append(f"- ... and {extra} more")
    return "\n".join(lines)


# =========================================================================== #
# Base class
# =========================================================================== #
class AiProvider(ABC):
    """Provider-agnostic structured generation with retries and self-repair."""

    name: str = "base"

    def __init__(
        self,
        *,
        max_retries: int = 3,
        max_repair_attempts: int = 2,
        request_timeout_s: float = 60.0,
        backoff_base_s: float = 1.0,
    ) -> None:
        self.max_retries = max(1, max_retries)
        self.max_repair_attempts = max(0, max_repair_attempts)
        self.request_timeout_s = request_timeout_s
        self.backoff_base_s = backoff_base_s

    # ---- the only thing a new provider must implement ---------------------- #
    @abstractmethod
    async def _generate_json(
        self,
        *,
        prompt: str,
        schema: type[BaseModel],
        system_prompt: str | None,
        temperature: float,
    ) -> str:
        """Return the model's raw JSON text for ``prompt``, constrained to ``schema``.

        Must translate SDK errors: 429 -> ``ProviderRateLimitError``, timeouts/5xx ->
        ``ProviderUnavailableError``, anything else -> ``ProviderError``.
        """

    # ---- public API -------------------------------------------------------- #
    async def generate_structured(
        self,
        *,
        prompt: str,
        schema: type[T],
        system_prompt: str | None = None,
        validation_context: Mapping[str, Any] | None = None,
        temperature: float = 0.2,
    ) -> T:
        """Generate an instance of ``schema``, validated against ``validation_context``.

        ``validation_context`` is passed to Pydantic (``info.context``), which is how
        ``StudyPlanDraft`` learns the request's ``duration_days`` / ``daily_minutes``.
        If validation fails, the model is re-prompted with the exact errors, up to
        ``max_repair_attempts`` times, before ``StructuredOutputError`` is raised.
        """
        current_prompt = prompt
        last_errors = ""

        for attempt in range(self.max_repair_attempts + 1):
            raw = await self._call_with_retries(
                prompt=current_prompt,
                schema=schema,
                system_prompt=system_prompt,
                temperature=temperature,
            )
            try:
                return schema.model_validate_json(
                    _strip_code_fences(raw), context=validation_context
                )
            except ValidationError as exc:
                last_errors = _format_validation_errors(exc)
                logger.warning(
                    "%s: output failed validation (attempt %d/%d, %d error(s))",
                    self.name,
                    attempt + 1,
                    self.max_repair_attempts + 1,
                    exc.error_count(),
                )
                current_prompt = self._build_repair_prompt(prompt, raw, last_errors)

        raise StructuredOutputError(
            f"{self.name}: no valid {schema.__name__} after "
            f"{self.max_repair_attempts + 1} attempt(s). Last errors:\n{last_errors}"
        )

    # ---- internals --------------------------------------------------------- #
    @staticmethod
    def _build_repair_prompt(original_prompt: str, bad_output: str, errors: str) -> str:
        return (
            f"{original_prompt}\n\n"
            "---\n"
            "Your previous answer was rejected by validation.\n"
            f"Previous answer:\n{bad_output}\n\n"
            f"Validation errors:\n{errors}\n\n"
            "Return a corrected answer as JSON only. Fix every error above and "
            "keep everything else grounded in the provided context."
        )

    async def _call_with_retries(
        self,
        *,
        prompt: str,
        schema: type[BaseModel],
        system_prompt: str | None,
        temperature: float,
    ) -> str:
        """Retry transient failures (429 / 5xx / timeout) with exponential backoff."""
        for attempt in range(1, self.max_retries + 1):
            try:
                return await asyncio.wait_for(
                    self._generate_json(
                        prompt=prompt,
                        schema=schema,
                        system_prompt=system_prompt,
                        temperature=temperature,
                    ),
                    timeout=self.request_timeout_s,
                )
            except TimeoutError as exc:
                error: ProviderError = ProviderUnavailableError(
                    f"{self.name}: request timed out after {self.request_timeout_s}s"
                )
                error.__cause__ = exc
            except (ProviderRateLimitError, ProviderUnavailableError) as exc:
                error = exc

            if attempt == self.max_retries:
                raise error

            delay = self.backoff_base_s * (2 ** (attempt - 1))
            delay += random.uniform(0, 0.25 * self.backoff_base_s)
            logger.warning(
                "%s: %s (attempt %d/%d), retrying in %.1fs",
                self.name,
                type(error).__name__,
                attempt,
                self.max_retries,
                delay,
            )
            await asyncio.sleep(delay)

        raise AssertionError("unreachable")  # pragma: no cover


# =========================================================================== #
# Gemini
# =========================================================================== #
class GeminiProvider(AiProvider):
    """Google Gemini via the ``google-genai`` SDK, using native JSON-schema output."""

    name = "gemini"

    def __init__(self, api_key: str, model: str = DEFAULT_GEMINI_MODEL, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if genai is None:
            raise ImportError("google-genai is not installed. Run: pip install google-genai")
        if not api_key:
            raise ValueError("GeminiProvider requires a non-empty api_key")
        self.model = model
        self._client = genai.Client(api_key=api_key)

    @classmethod
    def from_env(cls) -> GeminiProvider:
        """Build from GEMINI_API_KEY / GEMINI_MODEL / LLM_* env vars (see .env.example)."""
        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            raise ValueError("GEMINI_API_KEY is not set")
        return cls(
            api_key=api_key,
            model=os.environ.get("GEMINI_MODEL", DEFAULT_GEMINI_MODEL),
            max_retries=int(os.environ.get("LLM_MAX_RETRIES", "3")),
            max_repair_attempts=int(os.environ.get("LLM_MAX_REPAIR_ATTEMPTS", "2")),
            request_timeout_s=float(os.environ.get("LLM_REQUEST_TIMEOUT_S", "60")),
        )

    async def _generate_json(
        self,
        *,
        prompt: str,
        schema: type[BaseModel],
        system_prompt: str | None,
        temperature: float,
    ) -> str:
        config = genai_types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=temperature,
            response_mime_type="application/json",
            response_schema=schema,  # constrains decoding to the Pydantic JSON schema
        )
        try:
            response = await self._client.aio.models.generate_content(
                model=self.model, contents=prompt, config=config
            )
        except genai_errors.APIError as exc:
            raise self._map_error(exc) from exc

        # Empty text (e.g. a safety block) returns "" and falls into the repair loop.
        return response.text or ""

    def _map_error(self, exc: Exception) -> ProviderError:
        code = getattr(exc, "code", None)
        message = f"{self.name}: API error {code}"
        if code == 429:
            return ProviderRateLimitError(message)
        if isinstance(code, int) and (code >= 500 or code == 408):
            return ProviderUnavailableError(message)
        return ProviderError(message)  # 400/401/403/404: retrying will not help


# =========================================================================== #
# Groq
# =========================================================================== #
class GroqProvider(AiProvider):
    """Groq (OpenAI-compatible chat API) via the ``groq`` SDK.

    ``llama-3.3-70b-versatile`` supports JSON *mode* (valid JSON object) but not
    schema-constrained decoding, so the JSON Schema is embedded in the system prompt
    and conformance is enforced by the base class's Pydantic validation + repair loop.
    """

    name = "groq"

    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEFAULT_GROQ_MODEL,
        *,
        client: Any | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.model = model
        if client is not None:  # injected (tests, custom transport)
            self._client = client
            return
        if groq_sdk is None:
            raise ImportError("groq is not installed. Run: pip install groq")
        if not api_key:
            raise ValueError("GroqProvider requires a non-empty api_key")
        # max_retries=0: the SDK retries 429/5xx on its own, which would multiply with
        # (and hide from) our backoff layer. We own retries in AiProvider instead.
        self._client = groq_sdk.AsyncGroq(
            api_key=api_key, max_retries=0, timeout=self.request_timeout_s
        )

    @classmethod
    def from_env(cls) -> GroqProvider:
        """Build from GROQ_API_KEY / GROQ_MODEL / LLM_* env vars (see .env.example)."""
        api_key = os.environ.get("GROQ_API_KEY", "")
        if not api_key:
            raise ValueError("GROQ_API_KEY is not set")
        return cls(
            api_key=api_key,
            model=os.environ.get("GROQ_MODEL", DEFAULT_GROQ_MODEL),
            max_retries=int(os.environ.get("LLM_MAX_RETRIES", "3")),
            max_repair_attempts=int(os.environ.get("LLM_MAX_REPAIR_ATTEMPTS", "2")),
            request_timeout_s=float(os.environ.get("LLM_REQUEST_TIMEOUT_S", "60")),
        )

    async def _generate_json(
        self,
        *,
        prompt: str,
        schema: type[BaseModel],
        system_prompt: str | None,
        temperature: float,
    ) -> str:
        schema_json = json.dumps(schema.model_json_schema(), separators=(",", ":"))
        system = (
            (f"{system_prompt}\n\n" if system_prompt else "")
            + "Respond with a single JSON object only: no prose, no markdown fences. "
            + f"It must conform exactly to this JSON Schema:\n{schema_json}"
        )
        try:
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                temperature=temperature,
                response_format={"type": "json_object"},
            )
        except Exception as exc:
            mapped = self._map_error(exc)
            if mapped is None:
                raise  # not an SDK error: a bug on our side, do not disguise it
            raise mapped from exc

        # Empty content (or a truncated object) returns "" / bad JSON -> repair loop.
        return response.choices[0].message.content or ""

    def _map_error(self, exc: Exception) -> ProviderError | None:
        """Translate Groq SDK errors; ``None`` means "not a Groq SDK error"."""
        if groq_sdk is None:  # pragma: no cover - client was injected, SDK absent
            return None
        if isinstance(exc, groq_sdk.APIStatusError):
            code = getattr(exc, "status_code", None)
            message = f"{self.name}: API error {code}"
            if code == 429:
                return ProviderRateLimitError(message)
            if isinstance(code, int) and (code >= 500 or code == 408):
                return ProviderUnavailableError(message)
            return ProviderError(message)  # 400/401/403/404: retrying will not help
        if isinstance(exc, groq_sdk.APIConnectionError):  # includes APITimeoutError
            return ProviderUnavailableError(f"{self.name}: connection error")
        return None


# =========================================================================== #
# Fallback composite
# =========================================================================== #
class FallbackProvider(AiProvider):
    """Try providers in order; move on when one is exhausted (rate limit, outage,
    or persistently invalid output). Each wrapped provider keeps its own retry and
    repair behaviour, so this class only decides *who* to ask next."""

    name = "fallback"

    def __init__(self, providers: Sequence[AiProvider]) -> None:
        super().__init__()
        if not providers:
            raise ValueError("FallbackProvider needs at least one provider")
        self.providers = list(providers)

    async def generate_structured(  # type: ignore[override]
        self,
        *,
        prompt: str,
        schema: type[T],
        system_prompt: str | None = None,
        validation_context: Mapping[str, Any] | None = None,
        temperature: float = 0.2,
    ) -> T:
        last_error: ProviderError | None = None
        errors: list[ProviderError] = []
        for provider in self.providers:
            try:
                return await provider.generate_structured(
                    prompt=prompt,
                    schema=schema,
                    system_prompt=system_prompt,
                    validation_context=validation_context,
                    temperature=temperature,
                )
            except ProviderError as exc:
                last_error = exc
                errors.append(exc)
                logger.warning(
                    "provider %s failed (%s); trying next", provider.name, type(exc).__name__
                )
        # Every provider was rate limited -> surface it as such (HTTP 429, "retry with
        # backoff" in the project documentation); any other mix is a plain outage (503).
        if errors and all(isinstance(e, ProviderRateLimitError) for e in errors):
            raise ProviderRateLimitError(
                f"All providers rate limited: {[p.name for p in self.providers]}"
            ) from last_error
        raise ProviderUnavailableError(
            f"All providers failed: {[p.name for p in self.providers]}"
        ) from last_error

    async def _generate_json(self, **_: Any) -> str:  # pragma: no cover
        raise NotImplementedError("FallbackProvider delegates in generate_structured")
