from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
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
    _request_for_api_mode,
    estimate_tokens,
    parse_json_object,
)


class LLMParsingTests(unittest.TestCase):
    def test_json_mode_defaults_auto_and_respects_explicit_compatibility_choice(self):
        from simple_ar.integrations.llm import _json_response_format_mode
        self.assertEqual(LLMSettings().json_response_format, "auto")
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(_json_response_format_mode("SIMPLE_AR_JSON_RESPONSE_FORMAT"), "auto")
        with patch.dict(os.environ, {"SIMPLE_AR_JSON_RESPONSE_FORMAT": "off"}):
            self.assertEqual(_json_response_format_mode("SIMPLE_AR_JSON_RESPONSE_FORMAT"), "off")

    def test_auto_json_only_falls_back_for_explicit_format_rejection(self):
        client = LLMClient(LLMSettings(api_key="fixture", api_mode="chat"))
        with patch.object(client, "ask", side_effect=[LLMError("response_format json_object is not supported"), '{"ready":true}']) as ask:
            self.assertEqual(client.ask_json("system", "user"), {"ready": True})
            self.assertEqual(ask.call_count, 2)
            self.assertEqual(ask.call_args_list[0].kwargs["response_format"], {"type": "json_object"})
            self.assertNotIn("response_format", ask.call_args_list[1].kwargs)

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

    def test_literal_unknown_string_escapes_preserve_scientific_and_path_text(self) -> None:
        raw = r'{"math":"\(p\\lesssim0.02\)","path":"C:\work\q","nested":{"value":3}}'
        expected = {"math": r"\(p\lesssim0.02\)", "path": r"C:\work\q", "nested": {"value": 3}}
        for wrapper in (raw, "```json\n" + raw + "\n```", "Result:\n" + raw + "\nEnd."):
            with self.subTest(wrapper=wrapper):
                self.assertEqual(parse_json_object(wrapper), expected)
        self.assertIn(r"\(", raw)

    def test_valid_escapes_keep_the_standard_decoder_meaning(self) -> None:
        raw = r'{"text":"line\n\t\/\"\\","unicode":"\u00e9","braces":"{text}"}'
        self.assertEqual(parse_json_object(raw), json.loads(raw))
        self.assertEqual(parse_json_object("prefix " + raw + " suffix"), json.loads(raw))

    def test_damaged_outer_json_never_salvages_a_nested_record(self) -> None:
        for raw in (
            '{"broken":, "nested":{"looks_valid":true}}',
            '{"unclosed":{"looks_valid":true}',
            '{"broken":"unterminated, "nested":{"looks_valid":true}}',
            '[{"looks_valid":true},',
            'prefix [{"looks_valid":true}, suffix',
            '{"broken":,} {"looks_valid":true}',
            '```json\n{"broken":, "nested":{"looks_valid":true}}\n```',
        ):
            with self.subTest(raw=raw):
                self.assertIsNone(parse_json_object(raw))

    def test_unknown_escape_recovery_does_not_guess_unicode_controls_or_delimiters(self) -> None:
        for raw in (
            r'{"text":"\uNOTHEX","nested":{"valid":true}}',
            r'{"text":"\q\uNOTHEX","nested":{"valid":true}}',
            '{"text":"\\q\nunescaped newline","nested":{"valid":true}}',
            r'{"text":"\q","nested":{"valid":true}',
            r'{\q"nested":{"valid":true}}',
        ):
            with self.subTest(raw=raw):
                self.assertIsNone(parse_json_object(raw))

    def test_ask_json_literal_escape_recovery_does_not_send_another_request(self) -> None:
        client = LLMClient(LLMSettings(api_key="test-key", api_mode="chat"))
        raw = r'{"draft_markdown":"\(x\\in R\)","nested":{"value":4}}'
        response = {"choices": [{"message": {"content": raw}}]}
        with patch("simple_ar.integrations.llm._call_openai_sdk", return_value=response) as call:
            self.assertEqual(client.ask_json("system", "user"),
                {"draft_markdown": r"\(x\in R\)", "nested": {"value": 4}})
        self.assertEqual(call.call_count, 1)
        self.assertEqual(response["choices"][0]["message"]["content"], raw)

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

    def test_streamed_chat_chunks_are_assembled_and_usage_is_settled(self) -> None:
        client = LLMClient(LLMSettings(api_key="test-key", api_mode="chat", stream=True))
        chunks = iter([
            {"choices": [{"delta": {"content": "hel"}, "finish_reason": None}]},
            {"choices": [{"delta": {"content": "lo"}, "finish_reason": "stop"}],
             "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}},
        ])
        with patch("simple_ar.integrations.llm._call_openai_sdk", return_value=chunks) as call:
            self.assertEqual(client.ask("system", "user", label="stream-test"), "hello")

        request = call.call_args.args[1]
        self.assertTrue(request["stream"])
        self.assertEqual(request["extra_headers"]["Accept-Encoding"], "identity")

    def test_completed_stream_with_final_usage_does_not_wait_for_transport_eof(self):
        observed = []
        client = LLMClient(LLMSettings(api_key="test-key", api_mode="chat", stream=True),
                           usage_callback=observed.append)

        class CompletedStream:
            closed = False

            def __iter__(self):
                yield {"choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}}
                yield {"choices": [{"delta": {"content": "complete"}, "finish_reason": "stop"}]}
                yield {"choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}}
                raise AssertionError("Do not wait for another event after terminal content and usage")

            def close(self):
                self.closed = True

        stream = CompletedStream()
        with patch("simple_ar.integrations.llm._call_openai_sdk", return_value=stream):
            self.assertEqual(client.ask("system", "user"), "complete")
        self.assertTrue(stream.closed)
        self.assertEqual(observed[0].total_tokens, 5)

    def test_from_env_reads_stream_setting(self) -> None:
        with patch.dict(os.environ, {
            "OPENAI_API_KEY": "test-key",
            "SIMPLE_AR_LLM_API": "chat",
            "SIMPLE_AR_LLM_STREAM": "true",
        }, clear=True):
            client = LLMClient.from_env()

        self.assertTrue(client._settings.stream)

    def test_clean_eof_without_completion_is_not_adopted(self):
        ledger = BudgetLedger({"llm_requests": 1})
        client = LLMClient(LLMSettings(api_key="test", api_mode="chat", stream=True,
                                      retry_attempts=1), budget_ledger=ledger)
        with patch("simple_ar.integrations.llm._call_openai_sdk", return_value=iter([
            {"choices": [{"delta": {"content": '{"looks_complete": true}'}}]},
        ])):
            with self.assertRaisesRegex(LLMError, "without a completion marker"):
                client.ask("system", "user")
        self.assertEqual(ledger.entries[0].status, "unknown")

    def test_finished_generation_with_failed_accounting_tail_is_not_retried(self):
        observed = []
        client = LLMClient(LLMSettings(api_key="test", api_mode="chat", stream=True,
                                      retry_attempts=3), usage_callback=observed.append)
        def stream():
            yield {"choices": [{"delta": {"content": "complete"}, "finish_reason": "stop"}]}
            raise TimeoutError("accounting tail did not arrive")
        with patch("simple_ar.integrations.llm._call_openai_sdk", return_value=stream()) as call:
            self.assertEqual(client.ask("system", "user"), "complete")
        self.assertEqual(call.call_count, 1)
        self.assertEqual(observed[0].source, "estimated")

    def test_cancelled_provider_request_does_not_remain_inflight_or_retry(self):
        ledger = BudgetLedger({"llm_requests": 3})
        client = LLMClient(LLMSettings(api_key="test", api_mode="chat", retry_attempts=3),
                           budget_ledger=ledger)
        with patch("simple_ar.integrations.llm._call_openai_sdk", side_effect=KeyboardInterrupt) as call:
            with self.assertRaises(KeyboardInterrupt):
                client.ask("system", "user")
        self.assertEqual(call.call_count, 1)
        self.assertEqual(ledger.entries[0].status, "unknown")

    def test_sdk_client_lives_until_stream_consumed_and_closes_on_failure(self):
        for fail in (False, True):
            with self.subTest(fail=fail), patch("openai.OpenAI") as factory:
                client = factory.return_value
                def chunks():
                    client.close.assert_not_called()
                    if fail:
                        raise KeyboardInterrupt()
                    yield {"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}
                client.chat.completions.create.return_value = chunks()
                request = {"api_key": "test", "model": "test", "stream": True, "messages": []}
                if fail:
                    with self.assertRaises(KeyboardInterrupt):
                        _call_openai_sdk("chat", request)
                else:
                    response = _call_openai_sdk("chat", request)
                    self.assertEqual(response["choices"][0]["message"]["content"], "ok")
                client.close.assert_called_once()

    def test_optional_chat_thinking_switch_reaches_provider(self) -> None:
        client = LLMClient(LLMSettings(api_key="test-key", api_mode="chat", thinking_mode="disabled"))
        response = {"choices": [{"message": {"content": "ok"}}]}
        with patch("simple_ar.integrations.llm._call_openai_sdk", return_value=response) as call:
            self.assertEqual(client.ask("system", "user"), "ok")
        self.assertEqual(call.call_args.args[1]["extra_body"], {"thinking": {"type": "disabled"}})

    def test_thinking_switch_rejects_incompatible_api_or_effort(self) -> None:
        for settings in (
            LLMSettings(api_key="test-key", api_mode="responses", thinking_mode="disabled"),
            LLMSettings(api_key="test-key", api_mode="chat", thinking_mode="disabled", reasoning_effort="low"),
        ):
            with self.subTest(settings=settings), self.assertRaises(LLMError):
                LLMClient(settings).ask("system", "user")

    def test_from_env_reads_optional_thinking_switch(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key", "SIMPLE_AR_LLM_THINKING": "disabled"}, clear=True):
            self.assertEqual(LLMClient.from_env()._settings.thinking_mode, "disabled")

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

    def test_provider_worker_cap_applies_without_changing_batch_order(self) -> None:
        client = LLMClient(LLMSettings(api_key="test-key"))
        requests = [LLMRequest("s", str(index)) for index in range(3)]
        with patch.dict(os.environ, {"SIMPLE_AR_LLM_MAX_WORKERS": "1"}), patch.object(
            client, "ask_json", side_effect=lambda system, user, label="": {"index": int(user)}
        ), patch("simple_ar.integrations.llm.ThreadPoolExecutor", wraps=ThreadPoolExecutor) as pool:
            result = client.ask_json_many(requests, max_workers=3)
        self.assertEqual([row["index"] for row in result], [0, 1, 2])
        self.assertEqual(pool.call_args.kwargs["max_workers"], 1)

    def test_failed_batch_does_not_start_unsent_requests(self) -> None:
        client = LLMClient(LLMSettings(api_key="test-key"))
        calls: list[str] = []

        def fail_first(system: str, user: str, *, label: str = "") -> dict[str, str]:
            calls.append(user)
            raise LLMError("rate limited")

        requests = [LLMRequest("s", str(index), label=f"item-{index}") for index in range(3)]
        with patch.dict(os.environ, {"SIMPLE_AR_LLM_MAX_WORKERS": "1"}), patch.object(
            client, "ask_json", side_effect=fail_first
        ), self.assertRaisesRegex(LLMError, "item-0"):
            client.ask_json_many(requests, max_workers=3)
        self.assertEqual(calls, ["0"])

    def test_screening_worker_count_reflects_provider_cap(self) -> None:
        from simple_ar.research.evidence.screening import _screening_workers

        with patch.dict(os.environ, {"SIMPLE_AR_LLM_MAX_WORKERS": "1"}):
            self.assertEqual(_screening_workers({"llm_max_workers": 8, "read_screening_workers": 3}), 1)

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
                "SIMPLE_AR_LLM_REASONING_EFFORT": "low",
                "SIMPLE_AR_LLM_REASONING_OUTPUT_TOKENS": "8192",
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
        self.assertEqual(client._settings.reasoning_effort, "low")
        self.assertEqual(client._settings.reasoning_output_tokens, 8192)
        openai_call.assert_not_called()

    def test_reasoning_provider_options_do_not_raise_an_explicit_output_cap(self) -> None:
        client = LLMClient(
            LLMSettings(
                model="glm-5.3-flash",
                api_key="test-key",
                api_mode="chat",
                reasoning_effort="low",
                reasoning_output_tokens=8192,
            )
        )
        response = {"choices": [{"message": {"content": "ok"}}]}
        with patch("simple_ar.integrations.llm._call_openai_sdk", return_value=response) as call:
            self.assertEqual(client.ask("system", "user", max_output_tokens=1200), "ok")

        request = call.call_args.args[1]
        self.assertEqual(request["max_tokens"], 1200)
        self.assertEqual(request["extra_body"], {"reasoning_effort": "low"})

    def test_reasoning_output_cap_is_used_only_when_no_other_cap_exists(self) -> None:
        client = LLMClient(
            LLMSettings(
                model="glm-5.3-flash",
                api_key="test-key",
                api_mode="chat",
                reasoning_effort="low",
                reasoning_output_tokens=8192,
            )
        )
        response = {"choices": [{"message": {"content": "ok"}}]}
        with patch("simple_ar.integrations.llm._call_openai_sdk", return_value=response) as call:
            self.assertEqual(client.ask("system", "user"), "ok")

        request = call.call_args.args[1]
        self.assertEqual(request["max_tokens"], 8192)

    def test_reasoning_option_is_added_only_when_chat_mode_is_selected(self) -> None:
        request = {
            "model": "glm-5.3-flash",
            "instructions": "system",
            "input": [{"role": "user", "content": "user"}],
            "api_key": "test-key",
        }
        responses_request = _request_for_api_mode(
            request, "responses", reasoning_effort="low"
        )
        responses_stream_request = _request_for_api_mode(
            request, "responses", reasoning_effort="low", stream=True
        )
        chat_request = _request_for_api_mode(
            request, "chat", reasoning_effort="low"
        )

        self.assertNotIn("extra_body", responses_request)
        self.assertNotIn("stream", responses_stream_request)
        self.assertEqual(chat_request["extra_body"], {"reasoning_effort": "low"})

    def test_empty_reasoning_only_response_has_actionable_error(self) -> None:
        client = LLMClient(LLMSettings(api_key="test-key", api_mode="chat"))
        response = {
            "choices": [{
                "finish_reason": "length",
                "message": {
                    "content": "",
                    "reasoning_content": "internal reasoning",
                },
            }]
        }
        with patch("simple_ar.integrations.llm._call_openai_sdk", return_value=response):
            with self.assertRaisesRegex(LLMResponseError, "reasoning content"):
                client.ask("system", "user")

    def test_length_response_without_reasoning_has_output_cap_hint(self) -> None:
        client = LLMClient(LLMSettings(api_key="test-key", api_mode="chat"))
        response = {"choices": [{"finish_reason": "length", "message": {"content": ""}}]}
        with patch("simple_ar.integrations.llm._call_openai_sdk", return_value=response):
            with self.assertRaisesRegex(LLMResponseError, "Increase the per-call output token cap"):
                client.ask("system", "user")

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

    def test_default_env_uses_bounded_timeout_but_honors_explicit_output_cap(self) -> None:
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

        self.assertEqual(client._settings.request_timeout_sec, 180.0)
        self.assertIsNone(client._settings.max_output_tokens)

        response = {"choices": [{"message": {"content": "ok"}}]}
        with patch("simple_ar.integrations.llm._call_openai_sdk", return_value=response) as openai_call:
            output = client.ask("system", "user", max_output_tokens=999, label="uncapped-test")

        self.assertEqual(output, "ok")
        api_mode, request = openai_call.call_args.args
        self.assertEqual(api_mode, "chat")
        self.assertEqual(request["timeout"], 180.0)
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

    def test_overloaded_provider_retries_without_retrying_permanent_errors(self) -> None:
        client = LLMClient(LLMSettings(api_key="test-key", api_mode="chat", retry_attempts=2))
        response = {"choices": [{"message": {"content": "ok"}}]}

        with patch(
            "simple_ar.integrations.llm._call_openai_sdk",
            side_effect=[RuntimeError("Our servers are currently overloaded. Please try again later."), response],
        ) as call, patch("simple_ar.integrations.llm.time.sleep") as sleep:
            self.assertEqual(client.ask("system", "user", label="overload-retry"), "ok")

        self.assertEqual(call.call_count, 2)
        sleep.assert_called_once()

    def test_mid_response_disconnect_retries_and_preserves_unknown_usage(self) -> None:
        ledger = BudgetLedger({"llm_requests": 3, "total_tokens": 100})
        client = LLMClient(
            LLMSettings(api_key="test-key", api_mode="chat", retry_attempts=2,
                        max_output_tokens=10, retry_base_delay_sec=0.25),
            budget_ledger=ledger,
        )
        response = {"choices": [{"message": {"content": "ok"}}],
                    "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5}}
        disconnect = RuntimeError(
            "peer closed connection without sending complete message body (incomplete chunked read)"
        )
        with patch("simple_ar.integrations.llm._call_openai_sdk",
                   side_effect=[disconnect, response]) as call, patch(
                   "simple_ar.integrations.llm.time.sleep") as sleep:
            self.assertEqual(client.ask("system", "user", label="mid-response-retry"), "ok")
        self.assertEqual(call.call_count, 2)
        sleep.assert_called_once_with(0.25)
        self.assertEqual(ledger.entries[0].status, "unknown")
        self.assertEqual(ledger.entries[1].status, "settled")

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

    def test_stream_failure_retries_without_adopting_partial_text(self) -> None:
        ledger = BudgetLedger({"llm_requests": 3, "total_tokens": 200})
        client = LLMClient(
            LLMSettings(api_key="test-key", api_mode="chat", stream=True,
                        max_output_tokens=10, retry_attempts=2), budget_ledger=ledger,
        )

        class InterruptedStream:
            closed = False

            def __iter__(self):
                yield {"choices": [{"delta": {"content": "unfinished"}}]}
                raise RuntimeError("gateway decoder stopped unexpectedly")

            def close(self):
                self.closed = True

        interrupted = InterruptedStream()
        complete = iter([
            {"choices": [{"delta": {"content": "complete"}, "finish_reason": "stop"}]},
            {"choices": [], "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5}},
        ])
        with patch("simple_ar.integrations.llm._call_openai_sdk",
                   side_effect=[interrupted, complete]) as call, patch(
                       "simple_ar.integrations.llm.time.sleep"):
            self.assertEqual(client.ask("system", "user"), "complete")
        self.assertTrue(interrupted.closed)
        self.assertEqual(call.call_count, 2)
        self.assertEqual([row.status for row in ledger.entries], ["unknown", "settled"])
        self.assertEqual(ledger.entries[1].actual["total_tokens"], 5)

    def test_gateway_http2_failure_before_stream_iteration_is_uncertain_and_retryable(self) -> None:
        ledger = BudgetLedger({"llm_requests": 3, "total_tokens": 200})
        client = LLMClient(
            LLMSettings(api_key="test-key", api_mode="chat", stream=True,
                        max_output_tokens=10, retry_attempts=2), budget_ledger=ledger,
        )
        with patch("simple_ar.integrations.llm._call_openai_sdk", side_effect=[
            RuntimeError("Upstream HTTP/2 stream failed"),
            {"choices": [{"message": {"content": "ok"}}]},
        ]) as call, patch("simple_ar.integrations.llm.time.sleep"):
            self.assertEqual(client.ask("system", "user"), "ok")
        self.assertEqual(call.call_count, 2)
        self.assertEqual(ledger.entries[0].status, "unknown")

    def test_unclassified_provider_failure_does_not_imply_zero_charge_or_blind_retry(self) -> None:
        ledger = BudgetLedger({"llm_requests": 3, "total_tokens": 200})
        client = LLMClient(
            LLMSettings(api_key="test-key", api_mode="chat", max_output_tokens=10,
                        retry_attempts=3), budget_ledger=ledger,
        )
        with patch("simple_ar.integrations.llm._call_openai_sdk",
                   side_effect=RuntimeError("unexpected upstream failure")) as call:
            with self.assertRaises(LLMError):
                client.ask("system", "user")
        self.assertEqual(call.call_count, 1)
        self.assertEqual(ledger.entries[0].status, "unknown")
        self.assertEqual(ledger.entries[0].actual, {"llm_requests": 1})
        self.assertEqual(ledger.unknown_dimensions(), ("total_tokens",))

    def test_sdk_status_metadata_overrides_misleading_body_text(self) -> None:
        class ProviderFailure(RuntimeError):
            def __init__(self, status, message):
                super().__init__(message)
                self.status_code = status

        for status in (400, 401, 403):
            with self.subTest(status=status):
                ledger = BudgetLedger({"llm_requests": 3, "total_tokens": 200})
                client = LLMClient(LLMSettings(api_key="test-key", api_mode="chat",
                    retry_attempts=3), budget_ledger=ledger)
                with patch("simple_ar.integrations.llm._call_openai_sdk",
                           side_effect=ProviderFailure(status, "503 service unavailable mentioned in invalid input")) as call:
                    with self.assertRaises(LLMError):
                        client.ask("system", "user")
                self.assertEqual(call.call_count, 1)
                self.assertEqual(ledger.entries[0].status, "released")

        client = LLMClient(LLMSettings(api_key="test-key", api_mode="chat", retry_attempts=2))
        with patch("simple_ar.integrations.llm._call_openai_sdk", side_effect=[
            ProviderFailure(503, "unknown upstream response"),
            {"choices": [{"message": {"content": "ok"}}]},
        ]) as call, patch("simple_ar.integrations.llm.time.sleep"):
            self.assertEqual(client.ask("system", "user"), "ok")
        self.assertEqual(call.call_count, 2)

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
