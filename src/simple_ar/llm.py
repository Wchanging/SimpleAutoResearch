from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
import re
import threading
from dataclasses import dataclass
from typing import Any, Callable, Sequence, TypeVar

from dotenv import load_dotenv
from openai import OpenAI


T = TypeVar("T")
UsageCallback = Callable[["LLMUsage"], None]


class LLMError(RuntimeError):
    """Raised when the LLM layer cannot satisfy a request."""


@dataclass(frozen=True)
class LLMSettings:
    """Connection settings for an OpenAI-compatible chat provider.

    Args:
        model: Model name passed to the provider.
        api_key: API key used for authentication.
        base_url: Optional OpenAI-compatible API base URL.
        input_price_per_million: Optional input-token price used for local cost
            estimates.
        output_price_per_million: Optional output-token price used for local
            cost estimates.
    """

    model: str = "gpt-4o-mini"
    api_key: str = ""
    base_url: str = ""
    input_price_per_million: float | None = None
    output_price_per_million: float | None = None


@dataclass(frozen=True)
class LLMUsage:
    """Token usage for one LLM request.

    Args:
        model: Model used for the request.
        label: Optional caller-supplied request label.
        prompt_tokens: Input token count.
        completion_tokens: Output token count.
        total_tokens: Total token count.
        source: ``provider`` when usage came from the API, otherwise
            ``estimated``.
        estimated_cost_usd: Estimated USD cost when pricing is configured.
    """

    model: str
    label: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    source: str
    estimated_cost_usd: float | None = None

    def to_row(self) -> dict[str, Any]:
        """Convert usage into a JSON-serializable record."""
        return {
            "model": self.model,
            "label": self.label,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "source": self.source,
            "estimated_cost_usd": self.estimated_cost_usd,
        }


@dataclass(frozen=True)
class LLMRequest:
    """Single prompt request for text or JSON generation.

    Args:
        system: System instruction sent to the model.
        user: User prompt sent to the model.
        label: Optional identifier used in batch error messages.
    """

    system: str
    user: str
    label: str = ""


class LLMClient:
    """Small OpenAI SDK wrapper used by the pipeline stages.

    The wrapper keeps provider access explicit: one request in, one response
    out. Batch helpers only add bounded concurrency and preserve result order.
    """

    def __init__(
        self,
        settings: LLMSettings,
        *,
        usage_callback: UsageCallback | None = None,
    ) -> None:
        """Create a client from validated LLM settings.

        Args:
            settings: Provider connection settings.
            usage_callback: Optional callback invoked after each successful
                request with token usage metadata.

        Raises:
            LLMError: If the API key is missing.
        """
        if not settings.api_key:
            raise LLMError("OPENAI_API_KEY is not configured")
        kwargs: dict[str, str] = {"api_key": settings.api_key}
        if settings.base_url:
            kwargs["base_url"] = settings.base_url
        self._client = OpenAI(**kwargs)
        self.model = settings.model
        self._settings = settings
        self._usage_callback = usage_callback
        self._usage_lock = threading.Lock()

    @classmethod
    def from_env(
        cls,
        model: str | None = None,
        *,
        usage_callback: UsageCallback | None = None,
    ) -> "LLMClient":
        """Load provider settings from ``.env`` and environment variables.

        Args:
            model: Optional model override. When omitted, ``SIMPLE_AR_MODEL`` is
                used, falling back to ``gpt-4o-mini``.
            usage_callback: Optional usage callback for token accounting.

        Returns:
            Configured ``LLMClient`` instance.
        """
        load_dotenv()
        settings = LLMSettings(
            model=model or os.environ.get("SIMPLE_AR_MODEL", "gpt-4o-mini"),
            api_key=os.environ.get("OPENAI_API_KEY", ""),
            base_url=os.environ.get("OPENAI_BASE_URL", ""),
            input_price_per_million=_optional_float("SIMPLE_AR_INPUT_PRICE_PER_1M"),
            output_price_per_million=_optional_float("SIMPLE_AR_OUTPUT_PRICE_PER_1M"),
        )
        return cls(settings, usage_callback=usage_callback)

    def ask(self, system: str, user: str, *, label: str = "") -> str:
        """Send one text request to the model.

        Args:
            system: System instruction.
            user: User prompt.
            label: Optional label used in usage records.

        Returns:
            Model output with surrounding whitespace removed.

        Raises:
            LLMError: If both the Responses API and chat-completions fallback
                fail.
        """
        try:
            response = self._client.responses.create(
                model=self.model,
                input=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
            text = getattr(response, "output_text", "")
            if text:
                output = str(text).strip()
                self._record_usage(response, system, user, output, label=label)
                return output
        except Exception:
            # Some OpenAI-compatible endpoints support chat completions but not
            # the Responses API. Fall through to the compatibility path.
            pass

        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
            content = response.choices[0].message.content or ""
            output = content.strip()
            self._record_usage(response, system, user, output, label=label)
            return output
        except Exception as exc:
            raise LLMError(f"LLM request failed: {exc}") from exc

    def ask_json(self, system: str, user: str, *, label: str = "") -> dict[str, Any]:
        """Send one request and parse the response as a JSON object.

        Args:
            system: System instruction.
            user: User prompt. A JSON-only instruction is appended internally.
            label: Optional label used in usage records.

        Returns:
            Parsed JSON object.

        Raises:
            LLMError: If the request fails or no JSON object can be parsed.
        """
        raw = self.ask(
            system,
            user
            + "\n\nReturn valid JSON only. Do not include markdown or extra text.",
            label=label,
        )
        parsed = parse_json_object(raw)
        if parsed is None:
            raise LLMError("LLM response did not contain a JSON object")
        return parsed

    def ask_many(
        self,
        requests: Sequence[LLMRequest],
        *,
        max_workers: int = 4,
    ) -> list[str]:
        """Send multiple text requests concurrently.

        Args:
            requests: Prompt requests to execute.
            max_workers: Maximum number of worker threads for this batch.

        Returns:
            Text responses in the same order as ``requests``.

        Raises:
            LLMError: If any request fails.
        """
        return self._run_many(
            requests,
            lambda request: self.ask(request.system, request.user, label=request.label),
            max_workers=max_workers,
        )

    def ask_json_many(
        self,
        requests: Sequence[LLMRequest],
        *,
        max_workers: int = 4,
    ) -> list[dict[str, Any]]:
        """Send multiple JSON requests concurrently.

        Args:
            requests: Prompt requests to execute.
            max_workers: Maximum number of worker threads for this batch.

        Returns:
            Parsed JSON objects in the same order as ``requests``.

        Raises:
            LLMError: If any request fails or returns invalid JSON.
        """
        return self._run_many(
            requests,
            lambda request: self.ask_json(request.system, request.user, label=request.label),
            max_workers=max_workers,
        )

    def _run_many(
        self,
        requests: Sequence[LLMRequest],
        handler: Callable[[LLMRequest], T],
        *,
        max_workers: int,
    ) -> list[T]:
        """Execute a bounded batch of LLM requests while preserving order."""
        if not requests:
            return []
        if max_workers < 1:
            raise LLMError("max_workers must be at least 1")

        worker_count = min(max_workers, len(requests))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [executor.submit(handler, request) for request in requests]
            results: list[T] = []
            for request, future in zip(requests, futures):
                try:
                    results.append(future.result())
                except Exception as exc:
                    label = f" for {request.label}" if request.label else ""
                    raise LLMError(f"LLM batch request failed{label}: {exc}") from exc
            return results

    def _record_usage(
        self,
        response: object,
        system: str,
        user: str,
        output: str,
        *,
        label: str,
    ) -> None:
        """Record provider usage, falling back to local token estimates."""
        usage = _usage_from_response(response)
        if usage is None:
            prompt_tokens = estimate_tokens(system) + estimate_tokens(user)
            completion_tokens = estimate_tokens(output)
            total_tokens = prompt_tokens + completion_tokens
            source = "estimated"
        else:
            prompt_tokens, completion_tokens, total_tokens = usage
            source = "provider"
            if total_tokens < prompt_tokens + completion_tokens:
                total_tokens = prompt_tokens + completion_tokens

        record = LLMUsage(
            model=self.model,
            label=label,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            source=source,
            estimated_cost_usd=self._estimated_cost(prompt_tokens, completion_tokens),
        )
        if self._usage_callback is not None:
            with self._usage_lock:
                self._usage_callback(record)

    def _estimated_cost(self, prompt_tokens: int, completion_tokens: int) -> float | None:
        """Estimate request cost when caller has configured model pricing."""
        input_price = self._settings.input_price_per_million
        output_price = self._settings.output_price_per_million
        if input_price is None or output_price is None:
            return None
        cost = (prompt_tokens / 1_000_000 * input_price) + (
            completion_tokens / 1_000_000 * output_price
        )
        return round(cost, 8)


def parse_json_object(text: str) -> dict[str, Any] | None:
    """Extract a JSON object from plain, fenced, or lightly wrapped text.

    Args:
        text: Raw model output.

    Returns:
        Parsed JSON object, or ``None`` when no object can be recovered.
    """
    if not text.strip():
        return None

    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        pass

    fence = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL)
    if fence:
        try:
            value = json.loads(fence.group(1).strip())
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            pass

    candidates: list[str] = []
    depth = 0
    start = -1
    for index, char in enumerate(text):
        if char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                candidates.append(text[start : index + 1])
                start = -1

    for candidate in sorted(candidates, key=len, reverse=True):
        try:
            value = json.loads(candidate)
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            continue
    return None


def estimate_tokens(text: str) -> int:
    """Estimate token count from text using a conservative character heuristic.

    Args:
        text: Prompt or response text.

    Returns:
        Estimated token count. This is not model-tokenizer exact, but it is
        deterministic and dependency-free.
    """
    stripped = text.strip()
    if not stripped:
        return 0
    return max(1, (len(stripped) + 3) // 4)


def _usage_from_response(response: object) -> tuple[int, int, int] | None:
    """Extract token usage from Responses or chat-completions response objects."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return None

    prompt_tokens = _int_attr(usage, "input_tokens")
    if prompt_tokens is None:
        prompt_tokens = _int_attr(usage, "prompt_tokens")

    completion_tokens = _int_attr(usage, "output_tokens")
    if completion_tokens is None:
        completion_tokens = _int_attr(usage, "completion_tokens")

    total_tokens = _int_attr(usage, "total_tokens")
    if prompt_tokens is None or completion_tokens is None:
        return None
    if total_tokens is None:
        total_tokens = prompt_tokens + completion_tokens
    return prompt_tokens, completion_tokens, total_tokens


def _int_attr(obj: object, name: str) -> int | None:
    value = getattr(obj, name, None)
    if isinstance(value, int):
        return value
    return None


def _optional_float(env_name: str) -> float | None:
    value = os.environ.get(env_name, "").strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None
