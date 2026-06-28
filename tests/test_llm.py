from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from simple_ar.integrations.llm import (
    LLMClient,
    LLMError,
    LLMRequest,
    LLMSettings,
    estimate_tokens,
    parse_json_object,
)


class LLMParsingTests(unittest.TestCase):
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
        client = LLMClient(
            LLMSettings(
                api_key="test-key",
                api_mode="chat",
                transport_backend="litellm",
                retry_attempts=3,
                retry_base_delay_sec=0.25,
                retry_max_delay_sec=2.0,
            )
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

    def test_responses_transport_error_falls_back_to_chat(self) -> None:
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

    def test_default_env_omits_timeout_and_output_cap(self) -> None:
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
        self.assertNotIn("max_tokens", request)
        self.assertNotIn("max_completion_tokens", request)

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

    def test_estimate_tokens_is_deterministic_and_nonzero_for_text(self) -> None:
        self.assertEqual(estimate_tokens(""), 0)
        self.assertGreaterEqual(estimate_tokens("hello"), 1)


if __name__ == "__main__":
    unittest.main()
