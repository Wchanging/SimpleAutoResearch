from __future__ import annotations

import os
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from simple_ar.integrations.usage import record_usage, summarize_usage
from simple_ar.core.artifacts import read_json, read_jsonl
from simple_ar.core.budget import BudgetLedger
from simple_ar.integrations.llm import (
    LLMClient,
    LLMError,
    LLMResponseError,
    LLMRequest,
    LLMSettings,
    LLMUsage,
    _call_openai_sdk,
    estimate_tokens,
    parse_json_object,
)


class LLMParsingTests(unittest.TestCase):
    def test_json_format_failure_is_distinct_from_provider_failure(self):
        client = LLMClient(LLMSettings(api_key="test-key", api_mode="chat"))
        with patch.object(client, "ask", return_value="not JSON"):
            with self.assertRaises(LLMResponseError):
                client.ask_json("system", "user")
        failure = LLMError("provider timed out")
        with patch.object(client, "ask", side_effect=failure) as ask:
            with self.assertRaises(LLMError) as caught:
                client.ask_json("system", "user")
            self.assertIs(caught.exception, failure)
            self.assertEqual(ask.call_count, 1)

    def test_usage_recording_keeps_batch_projection_and_unknown_cost(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            meta = Path(tmp) / "meta"
            batch = Path(tmp) / "batch"
            messages = []
            record_usage(meta, LLMUsage("fixture", "plan", 10, 2, 12, "provider", 0.01),
                         stage="code_task.plan", message_callback=messages.append)
            record_usage(meta, LLMUsage("fixture", "edit", 20, 3, 23, "provider", provider_attempts=2),
                         stage="code_task.propose_edits", batch_dir=batch,
                         message_callback=messages.append)
            summary = read_json(meta / "llm_usage_summary.json")
            self.assertEqual(summary["requests"], 2)
            self.assertEqual(summary["total_tokens"], 35)
            self.assertEqual(summary["retry_count"], 1)
            self.assertIsNone(summary["estimated_cost_usd"])
            self.assertEqual(read_json(batch / "usage_summary.json")["total_tokens"], 23)
            self.assertEqual(read_jsonl(batch / "usage.jsonl"), read_jsonl(meta / "llm_usage.jsonl")[1:])
            self.assertIn("$0.010000", messages[0])
            self.assertNotIn("est cost", messages[1])

    def test_usage_summary_keeps_legacy_rows_and_counts_provider_retries(self) -> None:
        summary = summarize_usage(
            [
                {
                    "prompt_tokens": 3,
                    "completion_tokens": 2,
                    "total_tokens": 5,
                },
                {
                    "prompt_tokens": 4,
                    "completion_tokens": 1,
                    "total_tokens": 5,
                    "provider_attempts": 3,
                },
            ]
        )

        self.assertEqual(summary["requests"], 2)
        self.assertEqual(summary["provider_attempts"], 4)
        self.assertEqual(summary["retry_count"], 2)

    def test_parse_direct_json_object(self) -> None:
        self.assertEqual(parse_json_object('{"a": 1}'), {"a": 1})

    def test_parse_fenced_json_object(self) -> None:
        text = 'Here:\n```json\n{"a": 1, "b": "x"}\n```'
        self.assertEqual(parse_json_object(text), {"a": 1, "b": "x"})

    def test_parse_embedded_json_object(self) -> None:
        text = 'prefix {"a": {"nested": true}} suffix'
        self.assertEqual(parse_json_object(text), {"a": {"nested": True}})

    def test_reject_non_object_json(self) -> None:
        self.assertIsNone(parse_json_object("[1, 2, 3]"))

    def test_accept_single_object_array_from_compatible_gateway(self) -> None:
        self.assertEqual(parse_json_object('[{"a": 1}]'), {"a": 1})

    def test_ask_json_reads_chat_content_blocks(self) -> None:
        client = LLMClient(LLMSettings(api_key="test-key", api_mode="chat"))
        response = {
            "choices": [
                {
                    "message": {
                        "content": [{"type": "text", "text": '{"ok": true}'}],
                    }
                }
            ]
        }

        with patch("simple_ar.integrations.llm._call_openai_sdk", return_value=response):
            self.assertEqual(client.ask_json("system", "user"), {"ok": True})

    def test_ask_json_many_preserves_input_order(self) -> None:
        client = object.__new__(LLMClient)

        def fake_ask_json(system: str, user: str, *, label: str = "") -> dict[str, str]:
            return {"system": system, "user": user}

        client.ask_json = fake_ask_json
        requests = [
            LLMRequest(system="s", user="first", label="a"),
            LLMRequest(system="s", user="second", label="b"),
            LLMRequest(system="s", user="third", label="c"),
        ]

        results = LLMClient.ask_json_many(client, requests, max_workers=2)

        self.assertEqual([item["user"] for item in results], ["first", "second", "third"])

    def test_ask_many_rejects_invalid_worker_count(self) -> None:
        client = object.__new__(LLMClient)
        requests = [LLMRequest(system="s", user="u")]

        with self.assertRaises(LLMError):
            LLMClient.ask_many(client, requests, max_workers=0)

    def test_from_env_configures_provider_timeout(self) -> None:
        with patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "test-key",
                "OPENAI_BASE_URL": "https://example.test/v1",
                "SIMPLE_AR_MODEL": "test-model",
                "SIMPLE_AR_LLM_TIMEOUT_SEC": "42.5",
                "SIMPLE_AR_MAX_OUTPUT_TOKENS": "1234",
                "SIMPLE_AR_LLM_RETRY_ATTEMPTS": "5",
                "SIMPLE_AR_LLM_RETRY_BASE_DELAY_SEC": "0.5",
                "SIMPLE_AR_LLM_RETRY_MAX_DELAY_SEC": "9",
            },
            clear=True,
        ), patch("simple_ar.integrations.llm._call_openai_sdk") as openai_call:
            client = LLMClient.from_env()

        self.assertEqual(client.model, "test-model")
        self.assertEqual(client._settings.transport_backend, "openai")
        self.assertEqual(client._openai_model, "test-model")
        self.assertEqual(client._provider_model, "openai/test-model")
        self.assertEqual(client._settings.request_timeout_sec, 42.5)
        self.assertEqual(client._settings.max_output_tokens, 1234)
        self.assertEqual(client._settings.retry_attempts, 5)
        self.assertEqual(client._settings.retry_base_delay_sec, 0.5)
        self.assertEqual(client._settings.retry_max_delay_sec, 9)
        openai_call.assert_not_called()

    def test_ask_retries_transient_connection_error(self) -> None:
        usage: list[object] = []
        client = LLMClient(
            LLMSettings(
                api_key="test-key",
                api_mode="chat",
                transport_backend="litellm",
                retry_attempts=3,
                retry_base_delay_sec=0.25,
                retry_max_delay_sec=2.0,
            ),
            usage_callback=usage.append,
        )
        response = {"choices": [{"message": {"content": "ok"}}]}

        with patch(
            "simple_ar.integrations.llm.litellm.completion",
            side_effect=[RuntimeError("Connection error."), response],
        ) as completion, patch("simple_ar.integrations.llm.time.sleep") as sleep:
            output = client.ask("system", "user", label="retry-test")

        self.assertEqual(output, "ok")
        self.assertEqual(completion.call_count, 2)
        sleep.assert_called_once_with(0.25)
        self.assertEqual(len(usage), 1)
        self.assertEqual(usage[0].provider_attempts, 2)

    def test_ask_retries_timeout_and_cloudflare_524_with_capped_backoff(self) -> None:
        usage: list[object] = []
        client = LLMClient(
            LLMSettings(
                api_key="test-key",
                api_mode="chat",
                transport_backend="litellm",
                retry_attempts=3,
                retry_base_delay_sec=0.25,
                retry_max_delay_sec=0.5,
            ),
            usage_callback=usage.append,
        )
        response = {"choices": [{"message": {"content": "ok"}}]}

        with patch(
            "simple_ar.integrations.llm.litellm.completion",
            side_effect=[
                RuntimeError("Request timed out."),
                RuntimeError("Cloudflare 524 Origin Time-out."),
                response,
            ],
        ) as completion, patch("simple_ar.integrations.llm.time.sleep") as sleep:
            output = client.ask("system", "user", label="timeout-524-retry")

        self.assertEqual(output, "ok")
        self.assertEqual(completion.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [0.25, 0.5])
        self.assertEqual(len(usage), 1)
        self.assertEqual(usage[0].provider_attempts, 3)

    def test_auto_transport_error_falls_back_to_chat(self) -> None:
        usage: list[object] = []
        client = LLMClient(
            LLMSettings(
                api_key="test-key",
                api_mode="auto",
                transport_backend="litellm",
                retry_attempts=2,
                retry_base_delay_sec=0.25,
                retry_max_delay_sec=2.0,
            ),
            usage_callback=usage.append,
        )
        response = {"choices": [{"message": {"content": "ok"}}]}

        with patch(
            "simple_ar.integrations.llm.litellm.responses",
            side_effect=RuntimeError("Server disconnected without sending a response."),
        ) as responses, patch(
            "simple_ar.integrations.llm.litellm.completion",
            return_value=response,
        ) as completion, patch("simple_ar.integrations.llm.time.sleep") as sleep:
            output = client.ask("system", "user", label="compat-test")

        self.assertEqual(output, "ok")
        self.assertEqual(responses.call_count, 1)
        self.assertEqual(completion.call_count, 1)
        completion_request = completion.call_args.kwargs
        self.assertEqual(
            completion_request["messages"],
            [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "user"},
            ],
        )
        sleep.assert_not_called()
        self.assertEqual(len(usage), 1)
        self.assertEqual(usage[0].provider_attempts, 2)

    def test_responses_transport_error_stays_on_responses(self) -> None:
        client = LLMClient(
            LLMSettings(
                api_key="test-key",
                api_mode="responses",
                transport_backend="litellm",
                retry_attempts=2,
                retry_base_delay_sec=0.25,
                retry_max_delay_sec=2.0,
            )
        )

        with patch(
            "simple_ar.integrations.llm.litellm.responses",
            side_effect=RuntimeError("503 Service Unavailable"),
        ) as responses, patch(
            "simple_ar.integrations.llm.litellm.completion"
        ) as completion, patch("simple_ar.integrations.llm.time.sleep") as sleep:
            with self.assertRaises(LLMError):
                client.ask("system", "user", label="strict-responses-test")

        self.assertEqual(responses.call_count, 2)
        completion.assert_not_called()
        sleep.assert_called_once_with(0.25)

    def test_responses_disconnect_is_retried_in_strict_mode(self) -> None:
        client = LLMClient(
            LLMSettings(
                api_key="test-key",
                api_mode="responses",
                transport_backend="litellm",
                retry_attempts=2,
                retry_base_delay_sec=0.25,
                retry_max_delay_sec=2.0,
            )
        )

        with patch(
            "simple_ar.integrations.llm.litellm.responses",
            side_effect=RuntimeError("Server disconnected without sending a response."),
        ) as responses, patch(
            "simple_ar.integrations.llm.litellm.completion"
        ) as completion, patch("simple_ar.integrations.llm.time.sleep") as sleep:
            with self.assertRaises(LLMError):
                client.ask("system", "user", label="strict-disconnect-test")

        self.assertEqual(responses.call_count, 2)
        completion.assert_not_called()
        sleep.assert_called_once_with(0.25)

    def test_default_env_omits_implicit_timeout_but_honors_explicit_output_cap(self) -> None:
        with patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "test-key",
                "SIMPLE_AR_MODEL": "gpt-5.1",
                "SIMPLE_AR_LLM_API": "chat",
            },
            clear=True,
        ):
            client = LLMClient.from_env()

        self.assertIsNone(client._settings.request_timeout_sec)
        self.assertIsNone(client._settings.max_output_tokens)

        response = {"choices": [{"message": {"content": "ok"}}]}
        with patch("simple_ar.integrations.llm._call_openai_sdk", return_value=response) as openai_call:
            output = client.ask("system", "user", max_output_tokens=999, label="uncapped-test")

        self.assertEqual(output, "ok")
        api_mode, request = openai_call.call_args.args
        self.assertEqual(api_mode, "chat")
        self.assertNotIn("timeout", request)
        self.assertEqual(request["max_completion_tokens"], 999)

    def test_chat_cap_uses_completion_token_param_for_newer_models(self) -> None:
        client = LLMClient(
            LLMSettings(
                model="gpt-5.1",
                api_key="test-key",
                api_mode="chat",
                max_output_tokens=1000,
            )
        )
        response = {"choices": [{"message": {"content": "ok"}}]}

        with patch("simple_ar.integrations.llm._call_openai_sdk", return_value=response) as openai_call:
            output = client.ask("system", "user", max_output_tokens=80, label="cap-test")

        self.assertEqual(output, "ok")
        api_mode, request = openai_call.call_args.args
        self.assertEqual(api_mode, "chat")
        self.assertEqual(request["max_completion_tokens"], 80)
        self.assertNotIn("max_tokens", request)

    def test_invalid_per_call_output_cap_fails_before_provider_request(self) -> None:
        client = LLMClient(
            LLMSettings(
                api_key="test-key",
                api_mode="chat",
            )
        )

        with patch("simple_ar.integrations.llm._call_openai_sdk") as openai_call:
            with self.assertRaisesRegex(LLMError, "positive integer"):
                client.ask("system", "user", max_output_tokens=0)

        openai_call.assert_not_called()

    def test_budget_ledger_settles_successful_provider_attempt(self) -> None:
        ledger = BudgetLedger({"llm_requests": 2, "total_tokens": 100})
        client = LLMClient(
            LLMSettings(
                api_key="test-key",
                api_mode="chat",
                max_output_tokens=10,
                retry_attempts=1,
            ),
            budget_ledger=ledger,
            budget_session_id="session-1",
            budget_attempt_id="attempt-1",
        )
        response = {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
        }

        with patch(
            "simple_ar.integrations.llm._call_openai_sdk", return_value=response
        ) as openai_call:
            self.assertEqual(
                client.ask("system", "user", label="plan", budget_call_id="call-1"),
                "ok",
            )

        openai_call.assert_called_once()
        self.assertEqual(len(ledger.entries), 1)
        entry = ledger.entries[0]
        self.assertEqual(entry.status, "settled")
        self.assertEqual(entry.session_id, "session-1")
        self.assertEqual(entry.attempt_id, "attempt-1")
        self.assertEqual(entry.logical_call_id, "call-1")
        self.assertEqual(entry.purpose, "plan")
        self.assertEqual(entry.actual, {"llm_requests": 1, "total_tokens": 5})
        self.assertEqual(entry.actual_source, "provider")
        self.assertEqual(ledger.remaining("llm_requests"), 1)
        self.assertEqual(ledger.remaining("total_tokens"), 95)

    def test_with_budget_binds_a_client_copy_to_application_ledger(self) -> None:
        source = LLMClient(
            LLMSettings(api_key="test-key", api_mode="chat", max_output_tokens=10),
        )
        ledger = BudgetLedger({"llm_requests": 1, "total_tokens": 50})
        client = source.with_budget(ledger, session_id="session-1")
        response = {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
        }

        with patch("simple_ar.integrations.llm._call_openai_sdk", return_value=response):
            self.assertEqual(client.ask("system", "user", label="plan"), "ok")

        self.assertIsNot(client, source)
        self.assertEqual(ledger.entries[0].session_id, "session-1")
        self.assertEqual(ledger.entries[0].actual["total_tokens"], 5)

    def test_task_client_preserves_provider_and_budget_with_role_model_override(self) -> None:
        observed, local = [], []
        ledger = BudgetLedger({"llm_requests": 1, "total_tokens": 100})
        source = LLMClient(
            LLMSettings(api_key="test-key", base_url="https://provider.invalid/v1",
                        model="planner", api_mode="chat", max_output_tokens=10),
            usage_callback=observed.append, budget_ledger=ledger,
            budget_session_id="session-1", budget_attempt_id="implement-1",
        )
        client = LLMClient.for_task(client=source, model="reviewer", usage_callback=local.append)
        response = {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
        }
        with patch("simple_ar.integrations.llm._call_openai_sdk", return_value=response) as transport:
            self.assertEqual(client.ask("system", "user", label="review"), "ok")
        self.assertEqual(source.model, "planner")
        self.assertEqual(client.model, "reviewer")
        self.assertEqual(transport.call_args.args[1]["model"], "reviewer")
        self.assertEqual(observed, local)
        self.assertEqual(len(local), 1)
        self.assertEqual(len(ledger.entries), 1)
        self.assertEqual(ledger.entries[0].attempt_id, "implement-1")
        self.assertEqual(ledger.remaining("llm_requests"), 0)

    def test_budget_exhaustion_prevents_provider_call(self) -> None:
        ledger = BudgetLedger({"llm_requests": 0, "total_tokens": 100})
        client = LLMClient(
            LLMSettings(api_key="test-key", api_mode="chat"),
            budget_ledger=ledger,
        )

        with patch("simple_ar.integrations.llm._call_openai_sdk") as openai_call:
            with self.assertRaises(LLMError):
                client.ask("system", "user", label="blocked")

        openai_call.assert_not_called()
        self.assertEqual(ledger.entries, ())

    def test_retryable_provider_failure_is_counted_before_retry(self) -> None:
        ledger = BudgetLedger({"llm_requests": 3, "total_tokens": 100})
        client = LLMClient(
            LLMSettings(
                api_key="test-key",
                api_mode="chat",
                max_output_tokens=10,
                retry_attempts=2,
                retry_base_delay_sec=0.25,
            ),
            budget_ledger=ledger,
        )
        response = {"choices": [{"message": {"content": "ok"}}]}

        with patch(
            "simple_ar.integrations.llm._call_openai_sdk",
            side_effect=[RuntimeError("503 Service Unavailable"), response],
        ) as openai_call, patch("simple_ar.integrations.llm.time.sleep"):
            self.assertEqual(client.ask("system", "user", label="retry"), "ok")

        self.assertEqual(openai_call.call_count, 2)
        self.assertEqual([entry.status for entry in ledger.entries], ["settled", "settled"])
        self.assertEqual(ledger.entries[0].actual_source, "estimated")
        self.assertEqual(ledger.entries[0].actual["llm_requests"], 1)
        self.assertEqual(ledger.entries[1].actual["llm_requests"], 1)
        self.assertEqual(ledger.remaining("llm_requests"), 1)

    def test_provider_timeout_marks_attempt_unknown(self) -> None:
        ledger = BudgetLedger({"llm_requests": 3, "total_tokens": 100})
        client = LLMClient(
            LLMSettings(
                api_key="test-key",
                api_mode="chat",
                retry_attempts=1,
            ),
            budget_ledger=ledger,
        )

        with patch(
            "simple_ar.integrations.llm._call_openai_sdk",
            side_effect=RuntimeError("Request timed out."),
        ) as openai_call:
            with self.assertRaises(LLMError):
                client.ask("system", "user", label="timeout")

        openai_call.assert_called_once()
        self.assertEqual(ledger.entries[0].status, "unknown")
        self.assertEqual(ledger.remaining("llm_requests"), 2)
        self.assertIsNone(ledger.remaining("total_tokens"))

    def test_capped_timeout_can_retry_with_reserved_unknown_usage(self) -> None:
        ledger = BudgetLedger({"llm_requests": 3, "total_tokens": 100})
        client = LLMClient(
            LLMSettings(api_key="test-key", api_mode="chat", max_output_tokens=10,
                        retry_attempts=3, retry_base_delay_sec=0),
            budget_ledger=ledger,
        )
        response = {"choices": [{"message": {"content": "ok"}}],
                    "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5}}
        with patch("simple_ar.integrations.llm._call_openai_sdk",
                   side_effect=[TimeoutError("timed out"), response]) as call:
            self.assertEqual(client.ask("system", "user"), "ok")
        self.assertEqual(call.call_count, 2)
        self.assertEqual(ledger.remaining("llm_requests"), 1)
        self.assertEqual(ledger.entries[0].status, "unknown")
        self.assertEqual(ledger.unknown_dimensions(), ("total_tokens",))
        self.assertEqual(ledger.remaining("total_tokens"),
                         95 - ledger.entries[0].reserved["total_tokens"])

    def test_authentication_failure_releases_preflight_reservation(self) -> None:
        ledger = BudgetLedger({"llm_requests": 2, "total_tokens": 100})
        client = LLMClient(
            LLMSettings(api_key="test-key", api_mode="chat", retry_attempts=3),
            budget_ledger=ledger,
        )

        with patch(
            "simple_ar.integrations.llm._call_openai_sdk",
            side_effect=RuntimeError("Authentication failed: invalid API key."),
        ) as openai_call:
            with self.assertRaises(LLMError):
                client.ask("system", "user", label="auth")

        openai_call.assert_called_once()
        self.assertEqual(ledger.entries[0].status, "released")
        self.assertEqual(ledger.remaining("llm_requests"), 2)

    def test_ask_does_not_retry_permanent_auth_error(self) -> None:
        client = LLMClient(LLMSettings(api_key="test-key", api_mode="chat", retry_attempts=3))

        with patch(
            "simple_ar.integrations.llm._call_openai_sdk",
            side_effect=RuntimeError("Authentication failed: invalid API key."),
        ) as openai_call, patch("simple_ar.integrations.llm.time.sleep") as sleep:
            with self.assertRaises(LLMError):
                client.ask("system", "user", label="auth-test")

        self.assertEqual(openai_call.call_count, 1)
        sleep.assert_not_called()

    def test_gateway_model_routing_error_is_retryable(self) -> None:
        client = LLMClient(LLMSettings(api_key="test-key", api_mode="chat", retry_attempts=2))
        response = {"choices": [{"message": {"content": "ok"}}]}

        with patch(
            "simple_ar.integrations.llm._call_openai_sdk",
            side_effect=[
                RuntimeError("503 model not found: no available channel under distributor"),
                response,
            ],
        ) as openai_call, patch("simple_ar.integrations.llm.time.sleep") as sleep:
            self.assertEqual(client.ask("system", "user", label="gateway-retry"), "ok")

        self.assertEqual(openai_call.call_count, 2)
        sleep.assert_called_once()

    def test_openai_sdk_backend_disables_hidden_retries(self) -> None:
        with patch("openai.OpenAI") as openai_cls:
            openai_cls.return_value.chat.completions.create.return_value = {
                "choices": [{"message": {"content": "ok"}}]
            }

            _call_openai_sdk(
                "chat",
                {
                    "model": "gpt-5.1",
                    "api_key": "test-key",
                    "base_url": "https://example.test/v1",
                    "messages": [{"role": "user", "content": "hello"}],
                },
            )

        openai_cls.assert_called_once()
        client_kwargs = openai_cls.call_args.kwargs
        self.assertEqual(client_kwargs["max_retries"], 0)
        self.assertEqual(client_kwargs["base_url"], "https://example.test/v1")
        self.assertNotIn("timeout", client_kwargs)

    def test_openai_sdk_backend_passes_explicit_timeout_only(self) -> None:
        with patch("openai.OpenAI") as openai_cls:
            openai_cls.return_value.chat.completions.create.return_value = {
                "choices": [{"message": {"content": "ok"}}]
            }

            _call_openai_sdk(
                "chat",
                {
                    "model": "gpt-5.1",
                    "api_key": "test-key",
                    "timeout": 123,
                    "messages": [{"role": "user", "content": "hello"}],
                },
            )

        openai_cls.assert_called_once()
        client_kwargs = openai_cls.call_args.kwargs
        self.assertEqual(client_kwargs["timeout"], 123)

    def test_estimate_tokens_is_deterministic_and_nonzero_for_text(self) -> None:
        self.assertEqual(estimate_tokens(""), 0)
        self.assertGreaterEqual(estimate_tokens("hello"), 1)


if __name__ == "__main__":
    unittest.main()
