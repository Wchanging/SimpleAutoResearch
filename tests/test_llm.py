from __future__ import annotations

import unittest

from simple_ar.llm import parse_json_object


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


if __name__ == "__main__":
    unittest.main()
