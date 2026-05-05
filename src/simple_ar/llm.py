from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI


class LLMError(RuntimeError):
    """Raised when the LLM layer cannot satisfy a request."""


@dataclass(frozen=True)
class LLMSettings:
    model: str = "gpt-4o-mini"
    api_key: str = ""
    base_url: str = ""


class LLMClient:
    """Tiny OpenAI SDK wrapper for Day 3.

    This is intentionally small: no model routing, no provider abstraction,
    no streaming, and no hidden agent behavior.
    """

    def __init__(self, settings: LLMSettings) -> None:
        if not settings.api_key:
            raise LLMError("OPENAI_API_KEY is not configured")
        kwargs: dict[str, str] = {"api_key": settings.api_key}
        if settings.base_url:
            kwargs["base_url"] = settings.base_url
        self._client = OpenAI(**kwargs)
        self.model = settings.model

    @classmethod
    def from_env(cls, model: str | None = None) -> "LLMClient":
        load_dotenv()
        settings = LLMSettings(
            model=model or os.environ.get("SIMPLE_AR_MODEL", "gpt-4o-mini"),
            api_key=os.environ.get("OPENAI_API_KEY", ""),
            base_url=os.environ.get("OPENAI_BASE_URL", ""),
        )
        return cls(settings)

    def ask(self, system: str, user: str) -> str:
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
                return str(text).strip()
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
            return content.strip()
        except Exception as exc:
            raise LLMError(f"LLM request failed: {exc}") from exc

    def ask_json(self, system: str, user: str) -> dict[str, Any]:
        raw = self.ask(
            system,
            user
            + "\n\nReturn valid JSON only. Do not include markdown or extra text.",
        )
        parsed = parse_json_object(raw)
        if parsed is None:
            raise LLMError("LLM response did not contain a JSON object")
        return parsed


def parse_json_object(text: str) -> dict[str, Any] | None:
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
