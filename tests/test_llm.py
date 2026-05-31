from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from simple_ar.integrations.llm import LLMClient, LLMError, LLMRequest, estimate_tokens, parse_json_object


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
            },
            clear=True,
        ), patch("simple_ar.integrations.llm.litellm.completion") as completion:
            client = LLMClient.from_env()

        self.assertEqual(client.model, "test-model")
        self.assertEqual(client._provider_model, "openai/test-model")
        self.assertEqual(client._settings.request_timeout_sec, 42.5)
        self.assertEqual(client._settings.max_output_tokens, 1234)
        completion.assert_not_called()

    def test_estimate_tokens_is_deterministic_and_nonzero_for_text(self) -> None:
        self.assertEqual(estimate_tokens(""), 0)
        self.assertGreaterEqual(estimate_tokens("hello"), 1)


if __name__ == "__main__":
    unittest.main()
