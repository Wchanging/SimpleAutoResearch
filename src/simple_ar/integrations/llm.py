from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
import re
import threading
import time
from dataclasses import dataclass, replace
from typing import Any, Callable, Sequence, TypeVar
from uuid import uuid4

os.environ.setdefault("LITELLM_LOG", "ERROR")
import litellm
from dotenv import load_dotenv

from simple_ar.core.budget import BudgetError, BudgetLedger


T = TypeVar("T")
UsageCallback = Callable[["LLMUsage"], None]


class LLMError(RuntimeError):
    """Raised when the LLM layer cannot satisfy a request."""


class LLMResponseError(LLMError):
    """A completed response cannot satisfy the requested output format."""


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
        request_timeout_sec: Optional per-request provider timeout in
            seconds. ``None`` means do not pass a client-side timeout.
        max_output_tokens: Optional client-wide default output-token budget per
            request. ``None`` disables the client-wide default; an explicit
            per-call cap supplied by a pipeline step still applies.
        retry_attempts: Total provider attempts for transient transport/server
            failures. Includes the first request.
        retry_base_delay_sec: Initial exponential-backoff delay.
        retry_max_delay_sec: Maximum delay between provider retries.
        transport_backend: Provider client backend. ``openai`` uses the OpenAI
            Python SDK directly; ``litellm`` keeps the old compatibility path.
        api_mode: Provider API surface. ``responses`` uses Responses API-style
            ``instructions`` and ``input``. ``chat`` uses Chat Completions-style
            ``messages``. Each mode retries its own transient failures only.
            ``auto`` explicitly tries ``responses`` and then ``chat`` for
            compatibility with gateways that expose only one surface.
        json_response_format: JSON response-format mode for ``ask_json``.
            ``off`` keeps prompt-only JSON parsing for broad provider
            compatibility. ``auto`` tries provider-native JSON mode and retries
            without it only when the provider rejects the parameter.
            ``json_object`` always sends the parameter.
        chat_token_limit_param: Chat Completions output-limit parameter.
            ``auto`` uses ``max_completion_tokens`` for newer reasoning-style
            models such as GPT-5/o-series/Codex and ``max_tokens`` otherwise.
            Override only when a provider gateway requires a specific name.
    """

    model: str = "gpt-4o-mini"
    api_key: str = ""
    base_url: str = ""
    input_price_per_million: float | None = None
    output_price_per_million: float | None = None
    request_timeout_sec: float | None = None
    max_output_tokens: int | None = None
    retry_attempts: int = 3
    retry_base_delay_sec: float = 1.0
    retry_max_delay_sec: float = 12.0
    transport_backend: str = "openai"
    api_mode: str = "responses"
    json_response_format: str = "off"
    chat_token_limit_param: str = "auto"


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
        provider_attempts: Number of lower-level provider calls used for this
            successful ``ask()`` request, including transient retries and
            explicit API-surface attempts handled within that call.
    """

    model: str
    label: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    source: str
    estimated_cost_usd: float | None = None
    provider_attempts: int = 1

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
            "provider_attempts": self.provider_attempts,
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
    """Small OpenAI-compatible wrapper used by the pipeline stages.

    The wrapper keeps provider access explicit: one request in, one response
    out. Batch helpers only add bounded concurrency and preserve result order.
    """

    def __init__(
        self,
        settings: LLMSettings,
        *,
        usage_callback: UsageCallback | None = None,
        budget_ledger: BudgetLedger | None = None,
        budget_session_id: str = "",
        budget_attempt_id: str = "",
    ) -> None:
        """Create a client from validated LLM settings.

        Args:
            settings: Provider connection settings.
            usage_callback: Optional callback invoked after each successful
                request with token usage metadata.
            budget_ledger: Optional shared ledger. When supplied, each
                provider attempt reserves before transport and settles after a
                successful response.
            budget_session_id: Session identity recorded in ledger entries.
            budget_attempt_id: Attempt identity recorded in ledger entries.

        Raises:
            LLMError: If the API key is missing.
        """
        if not settings.api_key:
            raise LLMError("OPENAI_API_KEY is not configured")
        self.model = settings.model
        self._openai_model = settings.model.strip()
        self._provider_model = _litellm_model(settings)
        self._settings = settings
        self._usage_callback = usage_callback
        self._budget_ledger = budget_ledger
        self._budget_session_id = budget_session_id
        self._budget_attempt_id = budget_attempt_id
        self._budget_namespace = uuid4().hex[:12]
        self._budget_call_sequence = 0
        self._budget_reservation_sequence = 0
        self._usage_lock = threading.Lock()
        if self._settings.transport_backend == "litellm":
            litellm.suppress_debug_info = True

    @classmethod
    def from_env(
        cls,
        model: str | None = None,
        *,
        api_mode: str | None = None,
        usage_callback: UsageCallback | None = None,
        budget_ledger: BudgetLedger | None = None,
        budget_session_id: str = "",
        budget_attempt_id: str = "",
    ) -> "LLMClient":
        """Load provider settings from ``.env`` and environment variables.

        Args:
            model: Optional model override. When omitted, ``SIMPLE_AR_MODEL`` is
                used, falling back to ``gpt-4o-mini``.
            usage_callback: Optional usage callback for token accounting.
            budget_ledger: Optional shared ledger for bounded sessions.
            budget_session_id: Session identity recorded in ledger entries.
            budget_attempt_id: Attempt identity recorded in ledger entries.

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
            request_timeout_sec=_optional_positive_float("SIMPLE_AR_LLM_TIMEOUT_SEC", default=None),
            max_output_tokens=_optional_positive_int("SIMPLE_AR_MAX_OUTPUT_TOKENS", default=None),
            retry_attempts=_positive_int("SIMPLE_AR_LLM_RETRY_ATTEMPTS", default=3),
            retry_base_delay_sec=_positive_float("SIMPLE_AR_LLM_RETRY_BASE_DELAY_SEC", default=1.0),
            retry_max_delay_sec=_positive_float("SIMPLE_AR_LLM_RETRY_MAX_DELAY_SEC", default=12.0),
            transport_backend=_llm_transport_backend("SIMPLE_AR_LLM_BACKEND"),
            api_mode=_llm_api_mode_value(api_mode) if api_mode else _llm_api_mode("SIMPLE_AR_LLM_API"),
            json_response_format=_json_response_format_mode("SIMPLE_AR_JSON_RESPONSE_FORMAT"),
            chat_token_limit_param=_chat_token_limit_param_mode("SIMPLE_AR_CHAT_TOKEN_LIMIT_PARAM"),
        )
        return cls(
            settings,
            usage_callback=usage_callback,
            budget_ledger=budget_ledger,
            budget_session_id=budget_session_id,
            budget_attempt_id=budget_attempt_id,
        )

    def ask(
        self,
        system: str,
        user: str,
        *,
        label: str = "",
        max_output_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
        budget_call_id: str | None = None,
    ) -> str:
        """Send one text request to the model.

        Args:
            system: System instruction.
            user: User prompt.
            label: Optional label used in usage records.
            max_output_tokens: Optional per-request output cap. When omitted,
                the client-wide ``SIMPLE_AR_MAX_OUTPUT_TOKENS`` setting is used.
            response_format: Optional provider-native structured-output hint
                forwarded to LiteLLM/OpenAI-compatible providers.
            budget_call_id: Optional stable logical-call identity used by the
                shared budget ledger. A local identity is generated when it
                is omitted.

        Returns:
            Model output with surrounding whitespace removed.

        Raises:
            LLMError: If LiteLLM cannot complete the request.
        """
        request = self._build_request(system, user)
        if self._settings.base_url:
            request["api_base"] = self._settings.base_url
            request["base_url"] = self._settings.base_url
        # An explicit per-request cap is meaningful even when the client-wide
        # environment setting is unset.  The caller is allowed to bound one
        # expensive or structurally sensitive request without imposing the
        # same cap on every request made by this client.
        output_cap = max_output_tokens
        if output_cap is None:
            output_cap = self._settings.max_output_tokens
        if output_cap is not None:
            try:
                output_cap = int(output_cap)
            except (TypeError, ValueError) as exc:
                raise LLMError("max_output_tokens must be a positive integer") from exc
            if output_cap < 1:
                raise LLMError("max_output_tokens must be a positive integer")
            request["max_output_tokens"] = output_cap
        if response_format is not None:
            if self._settings.api_mode in {"responses", "auto"}:
                request["text"] = {"format": response_format}
            else:
                request["response_format"] = response_format
        prompt_tokens = estimate_tokens(system) + estimate_tokens(user)
        logical_call_id = budget_call_id or label or self._next_budget_call_id()
        response, provider_attempts, reservation_id = self._request_with_retry(
            request,
            logical_call_id=logical_call_id,
            prompt_tokens=prompt_tokens,
            output_cap=output_cap,
            label=label,
        )

        output = _content_from_response(response).strip()
        try:
            usage = self._build_usage_record(
                response,
                system,
                user,
                output,
                label=label,
                provider_attempts=provider_attempts,
            )
        except Exception as exc:
            self._mark_budget_unknown(reservation_id, reason=f"usage reconciliation failed: {exc}")
            raise
        self._settle_budget(reservation_id, usage)
        self._notify_usage(usage)
        return output

    def with_budget(
        self,
        budget_ledger: BudgetLedger,
        *,
        session_id: str = "",
        attempt_id: str = "",
    ) -> "LLMClient":
        """Return a client copy bound to one application's resource ledger."""

        return type(self)(
            self._settings,
            usage_callback=self._usage_callback,
            budget_ledger=budget_ledger,
            budget_session_id=session_id or self._budget_session_id,
            budget_attempt_id=attempt_id or self._budget_attempt_id,
        )

    @classmethod
    def for_task(
        cls, *, client: "LLMClient | None" = None,
        model: str | None = None, usage_callback: UsageCallback | None = None,
    ) -> "LLMClient":
        """Use an injected session client while preserving task usage reporting."""
        if client is None:
            return cls.from_env(model=model, usage_callback=usage_callback)

        def observe(usage: LLMUsage) -> None:
            if client._usage_callback is not None:
                client._usage_callback(usage)
            if usage_callback is not None and usage_callback is not client._usage_callback:
                usage_callback(usage)

        return cls(
            replace(client._settings, model=model or client.model),
            usage_callback=observe,
            budget_ledger=client._budget_ledger,
            budget_session_id=client._budget_session_id,
            budget_attempt_id=client._budget_attempt_id,
        )

    def _build_request(self, system: str, user: str) -> dict[str, Any]:
        if self._settings.api_mode in {"responses", "auto"}:
            request = {
                "model": self._model_for_backend(),
                "instructions": system,
                "input": [{"role": "user", "content": user}],
                "api_key": self._settings.api_key,
            }
            if self._settings.request_timeout_sec is not None:
                request["timeout"] = self._settings.request_timeout_sec
            return request
        request = {
            "model": self._model_for_backend(),
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "api_key": self._settings.api_key,
        }
        if self._settings.request_timeout_sec is not None:
            request["timeout"] = self._settings.request_timeout_sec
        return request

    def _model_for_backend(self) -> str:
        if self._settings.transport_backend == "litellm":
            return self._provider_model
        return self._openai_model

    def _request_with_retry(
        self,
        request: dict[str, Any],
        *,
        logical_call_id: str = "",
        prompt_tokens: int = 0,
        output_cap: int | None = None,
        label: str = "",
    ) -> tuple[object, int, str | None]:
        """Call the provider with bounded backoff and optional accounting."""
        attempts = max(1, int(self._settings.retry_attempts or 1))
        last_error: Exception | None = None
        mode_attempts: list[tuple[str, int, Exception]] = []
        provider_attempts = 0
        reserved_total_tokens = prompt_tokens + (output_cap or 0)
        for api_mode in _api_attempt_order(self._settings.api_mode):
            mode_request = _request_for_api_mode(
                request,
                api_mode,
                chat_token_limit_param=self._settings.chat_token_limit_param,
            )
            attempted = 0
            for attempt in range(1, attempts + 1):
                attempted = attempt
                provider_attempts += 1
                reservation_id = self._reserve_budget(
                    logical_call_id=logical_call_id,
                    label=label,
                    provider_attempt=provider_attempts,
                    reserved_total_tokens=reserved_total_tokens,
                )
                try:
                    return (
                        _call_provider(
                            self._settings.transport_backend,
                            api_mode,
                            mode_request,
                        ),
                        provider_attempts,
                        reservation_id,
                    )
                except Exception as exc:
                    last_error = exc
                    self._reconcile_failed_budget_attempt(
                        reservation_id,
                        exc,
                        reserved_total_tokens=reserved_total_tokens,
                        has_output_cap=output_cap is not None,
                    )
                    if attempt >= attempts or not _is_transient_llm_error(exc):
                        break
                    if (
                        self._settings.api_mode == "auto"
                        and api_mode == "responses"
                        and _is_response_transport_disconnect(exc)
                    ):
                        break
                    time.sleep(_retry_delay(self._settings, attempt))
            if last_error is None:
                continue
            mode_attempts.append((api_mode, attempted, last_error))
            if not _is_transient_llm_error(last_error):
                break
        if last_error is not None and _is_timeout_error(last_error):
            raise LLMError(
                f"LLM request timed out after {_attempt_summary(mode_attempts)} attempt(s): {last_error}"
            ) from last_error
        raise LLMError(
            f"LLM request failed after {_attempt_summary(mode_attempts)} attempt(s): {last_error}"
        ) from last_error

    def _reserve_budget(
        self,
        *,
        logical_call_id: str,
        label: str,
        provider_attempt: int,
        reserved_total_tokens: int,
    ) -> str | None:
        if self._budget_ledger is None:
            return None
        reservation_id = self._next_budget_reservation_id()
        try:
            self._budget_ledger.reserve(
                reservation_id,
                {
                    "llm_requests": 1,
                    "total_tokens": reserved_total_tokens,
                },
                session_id=self._budget_session_id,
                attempt_id=self._budget_attempt_id,
                logical_call_id=logical_call_id,
                purpose=label or "llm_request",
            )
        except BudgetError as exc:
            raise LLMError(
                f"LLM budget prevented provider attempt {provider_attempt}"
                f" for {label or logical_call_id}: {exc}"
            ) from exc
        return reservation_id

    def _reconcile_failed_budget_attempt(
        self,
        reservation_id: str | None,
        error: Exception,
        *,
        reserved_total_tokens: int,
        has_output_cap: bool,
    ) -> None:
        if self._budget_ledger is None or reservation_id is None:
            return
        reason = f"provider attempt failed: {type(error).__name__}: {error}"
        try:
            if _is_budget_consumption_unknown(error):
                self._budget_ledger.mark_unknown(
                    reservation_id, reason=reason,
                    known_actual={"llm_requests": 1},
                    retain_reservation=has_output_cap,
                )
            elif _is_transient_llm_error(error):
                # A retryable server/transport failure is still a physical
                # provider attempt.  No usage payload is available, so keep a
                # conservative estimate rather than silently dropping it.
                self._budget_ledger.settle(
                    reservation_id,
                    {
                        "llm_requests": 1,
                        "total_tokens": reserved_total_tokens,
                    },
                    actual_source="estimated",
                    reason=reason,
                )
            else:
                # Authentication and other local/provider rejections are known
                # not to have reached a billable successful request.
                self._budget_ledger.release(reservation_id, reason=reason)
        except BudgetError as exc:
            raise LLMError(f"Could not reconcile failed LLM budget attempt: {exc}") from exc

    def ask_json(
        self,
        system: str,
        user: str,
        *,
        label: str = "",
        max_output_tokens: int | None = None,
        budget_call_id: str | None = None,
    ) -> dict[str, Any]:
        """Send one request and parse the response as a JSON object.

        Args:
            system: System instruction.
            user: User prompt. A JSON-only instruction is appended internally.
            label: Optional label used in usage records.
            max_output_tokens: Optional per-request output cap. When omitted,
                the client-wide ``SIMPLE_AR_MAX_OUTPUT_TOKENS`` setting is used.
            budget_call_id: Optional stable logical-call identity for the
                shared budget ledger.

        Returns:
            Parsed JSON object.

        Raises:
            LLMError: If the request fails or no JSON object can be parsed.
        """
        json_user = user + "\n\nReturn valid JSON only. Do not include markdown or extra text."
        response_format = _json_response_format_request(self._settings.json_response_format)
        try:
            raw = self.ask(
                system,
                json_user,
                label=label,
                max_output_tokens=max_output_tokens,
                response_format=response_format,
                budget_call_id=budget_call_id,
            )
        except LLMError as exc:
            if self._settings.json_response_format == "auto" and _is_response_format_error(exc):
                raw = self.ask(
                    system,
                    json_user,
                    label=f"{label}-no-response-format" if label else "",
                    max_output_tokens=max_output_tokens,
                    budget_call_id=budget_call_id,
                )
            else:
                raise
        parsed = parse_json_object(raw)
        if parsed is None:
            raise LLMResponseError(
                "LLM response did not contain a JSON object "
                f"(received {len(raw.strip())} characters)"
            )
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
        provider_attempts: int = 1,
    ) -> LLMUsage:
        """Build and notify one usage record for compatibility callers."""
        record = self._build_usage_record(
            response,
            system,
            user,
            output,
            label=label,
            provider_attempts=provider_attempts,
        )
        self._notify_usage(record)
        return record

    def _build_usage_record(
        self,
        response: object,
        system: str,
        user: str,
        output: str,
        *,
        label: str,
        provider_attempts: int = 1,
    ) -> LLMUsage:
        """Build usage without notifying observers or changing state."""
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
            provider_attempts=max(1, int(provider_attempts)),
        )

        return record

    def _notify_usage(self, record: LLMUsage) -> None:
        """Notify the legacy usage observer exactly once after settlement."""
        if self._usage_callback is not None:
            with self._usage_lock:
                self._usage_callback(record)

    def _settle_budget(self, reservation_id: str | None, usage: LLMUsage) -> None:
        if self._budget_ledger is None or reservation_id is None:
            return
        try:
            self._budget_ledger.settle(
                reservation_id,
                {
                    "llm_requests": 1,
                    "total_tokens": usage.total_tokens,
                },
                actual_source=usage.source,
            )
        except BudgetError as exc:
            raise LLMError(f"Could not settle LLM usage in budget ledger: {exc}") from exc

    def _mark_budget_unknown(self, reservation_id: str | None, *, reason: str) -> None:
        if self._budget_ledger is None or reservation_id is None:
            return
        try:
            self._budget_ledger.mark_unknown(reservation_id, reason=reason)
        except BudgetError as exc:
            raise LLMError(f"Could not mark LLM budget usage unknown: {exc}") from exc

    def _next_budget_call_id(self) -> str:
        with self._usage_lock:
            self._budget_call_sequence += 1
            return f"llm-call-{self._budget_namespace}-{self._budget_call_sequence:06d}"

    def _next_budget_reservation_id(self) -> str:
        with self._usage_lock:
            self._budget_reservation_sequence += 1
            return f"llm-reservation-{self._budget_namespace}-{self._budget_reservation_sequence:06d}"

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
        if isinstance(value, dict):
            return value
        # A few OpenAI-compatible gateways wrap an otherwise valid structured
        # response in a one-item array.  Accepting that shape is safe while
        # still rejecting arbitrary arrays, because the public contract
        # remains a single JSON object.
        if isinstance(value, list) and len(value) == 1 and isinstance(value[0], dict):
            return value[0]
    except json.JSONDecodeError:
        pass

    fence = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL)
    if fence:
        try:
            value = json.loads(fence.group(1).strip())
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            pass

    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            value, _ = decoder.raw_decode(text[match.start() :])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value

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
    usage = _get_value(response, "usage")
    if usage is None:
        return None

    prompt_tokens = _int_value(usage, "input_tokens")
    if prompt_tokens is None:
        prompt_tokens = _int_value(usage, "prompt_tokens")

    completion_tokens = _int_value(usage, "output_tokens")
    if completion_tokens is None:
        completion_tokens = _int_value(usage, "completion_tokens")

    total_tokens = _int_value(usage, "total_tokens")
    if prompt_tokens is None or completion_tokens is None:
        return None
    if total_tokens is None:
        total_tokens = prompt_tokens + completion_tokens
    return prompt_tokens, completion_tokens, total_tokens


def _content_from_response(response: object) -> str:
    choices = _get_value(response, "choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        message = _get_value(first, "message")
        content = _get_value(message, "content") if message is not None else None
        if content is not None:
            return _text_from_content(content)
        text = _get_value(first, "text")
        if text is not None:
            return str(text)
    output_text = _get_value(response, "output_text")
    if output_text is not None:
        return str(output_text)
    output = _get_value(response, "output")
    if isinstance(output, list):
        parts: list[str] = []
        for item in output:
            content = _get_value(item, "content")
            if content is not None:
                text = _text_from_content(content)
                if text:
                    parts.append(text)
        if parts:
            return "\n".join(parts)
    return ""


def _text_from_content(content: object) -> str:
    """Return textual content from string or OpenAI-style content blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for chunk in content:
            text = _get_value(chunk, "text")
            if text is None:
                text = _get_value(chunk, "content")
            if text is not None:
                rendered = _text_from_content(text)
                if rendered:
                    parts.append(rendered)
        return "\n".join(parts)
    if isinstance(content, dict):
        text = _get_value(content, "text")
        if text is not None:
            return _text_from_content(text)
        nested = _get_value(content, "content")
        if nested is not None:
            return _text_from_content(nested)
        return ""
    return str(content) if content is not None else ""


def _int_value(obj: object, name: str) -> int | None:
    value = _get_value(obj, name)
    if isinstance(value, int):
        return value
    return None


def _get_value(obj: object, name: str) -> object | None:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _litellm_model(settings: LLMSettings) -> str:
    """Return the LiteLLM model string for default or custom OpenAI endpoints."""
    model = settings.model.strip()
    if settings.base_url and "/" not in model:
        return f"openai/{model}"
    return model


def _optional_float(env_name: str) -> float | None:
    value = os.environ.get(env_name, "").strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _positive_float(env_name: str, *, default: float) -> float:
    value = os.environ.get(env_name, "").strip()
    if not value:
        return default
    try:
        parsed = float(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _optional_positive_float(env_name: str, *, default: float | None) -> float | None:
    value = os.environ.get(env_name, "").strip().lower()
    if not value:
        return default
    if value in {"0", "false", "no", "none", "off", "disabled", "unlimited"}:
        return None
    try:
        parsed = float(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else None


def _optional_positive_int(env_name: str, *, default: int | None) -> int | None:
    value = os.environ.get(env_name, "").strip().lower()
    if not value:
        return default
    if value in {"0", "false", "no", "none", "off", "disabled", "unlimited"}:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else None


def _positive_int(env_name: str, *, default: int) -> int:
    value = os.environ.get(env_name, "").strip()
    if not value:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _json_response_format_mode(env_name: str) -> str:
    value = os.environ.get(env_name, "off").strip().lower().replace("-", "_")
    aliases = {
        "": "off",
        "auto": "auto",
        "1": "json_object",
        "true": "json_object",
        "yes": "json_object",
        "on": "json_object",
        "json": "json_object",
        "json_object": "json_object",
        "response_format": "json_object",
        "0": "off",
        "false": "off",
        "no": "off",
        "none": "off",
        "disabled": "off",
        "off": "off",
    }
    return aliases.get(value, "off")


def _llm_api_mode(env_name: str) -> str:
    return _llm_api_mode_value(os.environ.get(env_name, "responses"))


def _llm_api_mode_value(value: str | None) -> str:
    value = str(value or "responses").strip().lower().replace("-", "_")
    aliases = {
        "": "responses",
        "auto": "auto",
        "compat": "auto",
        "responses_then_chat": "auto",
        "response_then_chat": "auto",
        "chat": "chat",
        "completion": "chat",
        "completions": "chat",
        "chat_completion": "chat",
        "chat_completions": "chat",
        "messages": "chat",
        "responses": "responses",
        "response": "responses",
        "input": "responses",
    }
    return aliases.get(value, "responses")


def _llm_transport_backend(env_name: str) -> str:
    value = os.environ.get(env_name, "openai").strip().lower().replace("-", "_")
    aliases = {
        "": "openai",
        "openai": "openai",
        "sdk": "openai",
        "openai_sdk": "openai",
        "direct": "openai",
        "native": "openai",
        "litellm": "litellm",
        "lite_llm": "litellm",
        "compat": "litellm",
    }
    return aliases.get(value, "openai")


def _chat_token_limit_param_mode(env_name: str) -> str:
    value = os.environ.get(env_name, "auto").strip().lower().replace("-", "_")
    aliases = {
        "": "auto",
        "auto": "auto",
        "max_tokens": "max_tokens",
        "tokens": "max_tokens",
        "legacy": "max_tokens",
        "max_completion_tokens": "max_completion_tokens",
        "completion_tokens": "max_completion_tokens",
        "reasoning": "max_completion_tokens",
    }
    return aliases.get(value, "auto")


def _chat_token_limit_param(mode: str, model: str) -> str:
    normalized = (mode or "auto").strip().lower().replace("-", "_")
    if normalized in {"max_tokens", "max_completion_tokens"}:
        return normalized
    model_name = model.lower().removeprefix("openai/")
    reasoning_prefixes = ("gpt-5", "gpt-4.1", "o1", "o3", "o4", "codex")
    if model_name.startswith(reasoning_prefixes):
        return "max_completion_tokens"
    return "max_tokens"


def _api_attempt_order(api_mode: str) -> list[str]:
    mode = (api_mode or "responses").strip().lower().replace("-", "_")
    if mode == "chat":
        return ["chat"]
    if mode == "auto":
        return ["responses", "chat"]
    if mode == "responses":
        return ["responses"]
    return ["responses"]


def _call_provider(backend: str, api_mode: str, request: dict[str, Any]) -> object:
    if backend == "litellm":
        return _call_litellm(api_mode, request)
    if backend == "openai":
        return _call_openai_sdk(api_mode, request)
    raise ValueError(f"Unsupported LLM transport backend: {backend}")


def _call_litellm(api_mode: str, request: dict[str, Any]) -> object:
    if api_mode == "responses":
        return litellm.responses(**request)
    if api_mode == "chat":
        return litellm.completion(**request)
    raise ValueError(f"Unsupported LLM API mode: {api_mode}")


def _call_openai_sdk(api_mode: str, request: dict[str, Any]) -> object:
    from openai import OpenAI

    payload = dict(request)
    api_key = str(payload.pop("api_key", "") or "")
    base_url = payload.pop("base_url", None)
    api_base = payload.pop("api_base", None)
    base_url = base_url or api_base
    timeout = payload.pop("timeout", None) if "timeout" in payload else None
    client_kwargs: dict[str, Any] = {
        "api_key": api_key,
        # Keep retry accounting in SimpleAutoResearch. The OpenAI SDK defaults
        # to hidden internal retries, which can duplicate long planning calls
        # while the CLI only reports a single outer attempt.
        "max_retries": 0,
    }
    if timeout is not None:
        client_kwargs["timeout"] = timeout
    if base_url:
        client_kwargs["base_url"] = str(base_url)
    client = OpenAI(**client_kwargs)
    payload = _drop_none_values(payload)
    if api_mode == "responses":
        return client.responses.create(**payload)
    if api_mode == "chat":
        return client.chat.completions.create(**payload)
    raise ValueError(f"Unsupported LLM API mode: {api_mode}")


def _request_for_api_mode(
    request: dict[str, Any],
    api_mode: str,
    *,
    chat_token_limit_param: str = "auto",
) -> dict[str, Any]:
    if api_mode == "responses":
        return _as_responses_request(request)
    if api_mode == "chat":
        return _as_chat_request(request, chat_token_limit_param=chat_token_limit_param)
    raise ValueError(f"Unsupported LLM API mode: {api_mode}")


def _as_responses_request(request: dict[str, Any]) -> dict[str, Any]:
    if "instructions" in request and "input" in request:
        converted = dict(request)
    else:
        messages = request.get("messages")
        system = ""
        user_parts: list[str] = []
        if isinstance(messages, list):
            for message in messages:
                if not isinstance(message, dict):
                    continue
                role = str(message.get("role") or "")
                content = _message_content_text(message.get("content"))
                if role == "system":
                    system = content
                else:
                    user_parts.append(content)
        converted = {
            "model": request.get("model"),
            "instructions": system,
            "input": [{"role": "user", "content": "\n\n".join(part for part in user_parts if part)}],
            "api_key": request.get("api_key"),
            "timeout": request.get("timeout"),
        }
        _copy_optional_request_fields(request, converted, ("api_base", "base_url"))
        if "response_format" in request:
            converted["text"] = {"format": request["response_format"]}
    if "max_output_tokens" not in converted:
        if "max_completion_tokens" in converted:
            converted["max_output_tokens"] = converted["max_completion_tokens"]
        elif "max_tokens" in converted:
            converted["max_output_tokens"] = converted["max_tokens"]
    converted.pop("max_completion_tokens", None)
    converted.pop("max_tokens", None)
    return _drop_none_values(converted)


def _as_chat_request(request: dict[str, Any], *, chat_token_limit_param: str = "auto") -> dict[str, Any]:
    if "messages" in request:
        converted = dict(request)
    else:
        system = str(request.get("instructions") or "")
        user = _responses_input_text(request.get("input"))
        converted = {
            "model": request.get("model"),
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "api_key": request.get("api_key"),
            "timeout": request.get("timeout"),
        }
        _copy_optional_request_fields(request, converted, ("api_base", "base_url"))
        text_config = request.get("text")
        if isinstance(text_config, dict) and isinstance(text_config.get("format"), dict):
            converted["response_format"] = text_config["format"]
    output_cap = converted.pop("max_output_tokens", None)
    if output_cap is not None:
        converted.pop("max_tokens", None)
        converted.pop("max_completion_tokens", None)
        converted[_chat_token_limit_param(chat_token_limit_param, str(converted.get("model") or ""))] = output_cap
    return _drop_none_values(converted)


def _responses_input_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict):
                parts.append(_message_content_text(item.get("content")))
            else:
                parts.append(str(item))
        return "\n\n".join(part for part in parts if part)
    if value is None:
        return ""
    return str(value)


def _message_content_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict):
                text = item.get("text")
                if text is None:
                    text = item.get("content")
                if text is not None:
                    parts.append(str(text))
            elif item is not None:
                parts.append(str(item))
        return "\n".join(parts)
    if value is None:
        return ""
    return str(value)


def _copy_optional_request_fields(
    source: dict[str, Any], target: dict[str, Any], names: Sequence[str]
) -> None:
    for name in names:
        if name in source:
            target[name] = source[name]


def _drop_none_values(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item is not None}


def _attempt_summary(mode_attempts: Sequence[tuple[str, int, Exception]]) -> str:
    if not mode_attempts:
        return "0"
    return ", ".join(f"{mode}={attempts}" for mode, attempts, _ in mode_attempts)


def _json_response_format_request(mode: str) -> dict[str, Any] | None:
    return {"type": "json_object"} if mode in {"auto", "json_object"} else None


def _retry_delay(settings: LLMSettings, attempt: int) -> float:
    base = settings.retry_base_delay_sec if settings.retry_base_delay_sec > 0 else 1.0
    maximum = settings.retry_max_delay_sec if settings.retry_max_delay_sec > 0 else base
    return min(maximum, base * (2 ** max(0, attempt - 1)))


def _is_transient_llm_error(exc: Exception) -> bool:
    name = type(exc).__name__.lower()
    message = str(exc).lower()
    transient_markers = (
        "timeout",
        "timed out",
        "connection error",
        "connection reset",
        "connection aborted",
        "connection refused",
        "api connection",
        "service unavailable",
        "temporarily unavailable",
        "rate limit",
        "ratelimit",
        "too many requests",
        "internalservererror",
        "internal server error",
        "bad gateway",
        "gateway timeout",
        "origin timeout",
        "origin time-out",
        "server disconnected",
        "remote protocol error",
        "httpstatuserror",
        "429",
        "500",
        "502",
        "503",
        "504",
        "524",
    )
    permanent_markers = (
        "authentication",
        "invalid api key",
        "permission denied",
        "not found",
        "context_length",
        "context length",
        "invalid request",
        "badrequest",
        "400",
        "401",
        "403",
        "404",
    )
    # Some OpenAI-compatible gateways report a temporarily unavailable
    # routing channel as "model not found". Treat that precise combination as
    # retryable before applying the generic permanent "not found" rule.
    gateway_unavailable = (
        "no available channel",
        "no available provider",
        "distributor",
    )
    if any(marker in message for marker in gateway_unavailable) and any(
        marker in message for marker in ("429", "500", "502", "503", "504", "service unavailable")
    ):
        return True
    if any(marker in message or marker in name for marker in permanent_markers):
        return False
    return any(marker in message or marker in name for marker in transient_markers)


def _is_response_transport_disconnect(exc: Exception) -> bool:
    name = type(exc).__name__.lower()
    message = str(exc).lower()
    markers = (
        "server disconnected",
        "remote protocol",
        "remoteprotocolerror",
        "connection reset",
        "connection aborted",
    )
    return any(marker in message or marker in name for marker in markers)


def _is_budget_consumption_unknown(exc: Exception) -> bool:
    """Return whether a failed call may have reached the provider."""

    return _is_response_transport_disconnect(exc) or _is_timeout_error(exc)


def _is_timeout_error(exc: Exception) -> bool:
    name = type(exc).__name__.lower()
    message = str(exc).lower()
    return "timeout" in name or "timed out" in message or "timeout" in message


def _is_response_format_error(exc: Exception) -> bool:
    message = str(exc).lower()
    markers = (
        "response_format",
        "response format",
        "json_object",
        "json object",
        "structured output",
        "structured outputs",
    )
    rejection_markers = (
        "unsupported",
        "not supported",
        "unrecognized",
        "unknown parameter",
        "extra inputs are not permitted",
        "invalid request",
        "badrequest",
        "400",
    )
    return any(marker in message for marker in markers) and any(
        marker in message for marker in rejection_markers
    )
