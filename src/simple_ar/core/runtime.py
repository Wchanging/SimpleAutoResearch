"""Small runtime helpers shared by capability adapters and legacy stages.

These helpers operate on the existing pipeline ``Context`` because the
legacy CLI still uses that context. Keeping them here prevents new
capability modules from importing ``pipeline_stages`` merely to read an
artifact or initialize the configured LLM client. The old module re-exports
the names for compatibility with frozen stage code.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from simple_ar.app.usage import record_llm_usage
from simple_ar.core.artifacts import read_json, read_jsonl, read_text
from simple_ar.core.pipeline import Context
from simple_ar.integrations.llm import LLMClient, LLMError


def ensure_heading(markdown: str, heading: str) -> str:
    stripped = markdown.strip()
    if stripped.startswith("#"):
        return stripped + "\n"
    return f"# {heading}\n\n{stripped}\n"


def list_value(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def llm_client(ctx: Context) -> LLMClient | None:
    """Create the configured LLM client and attach run-level usage tracking."""

    if ctx.config.get("use_llm") is not True:
        return None
    model_value = ctx.config.get("model")
    model = str(model_value) if model_value else None
    try:
        return LLMClient.from_env(
            model=model,
            usage_callback=lambda usage: record_llm_usage(ctx, usage),
        )
    except LLMError as exc:
        handle_llm_failure(ctx, "LLM client initialization failed", exc)
        return None


def handle_llm_failure(ctx: Context, message: str, exc: Exception) -> None:
    """Raise in strict online mode; otherwise announce explicit fallback."""

    if not llm_fallback_allowed(ctx):
        raise LLMError(
            f"{message}: {exc}. LLM fallback is disabled; retry the same run or use --no-llm explicitly."
        ) from exc
    ctx.emit("stage_message", f"{message}; using explicit offline fallback. {exc}")


def llm_fallback_allowed(ctx: Context) -> bool:
    return ctx.config.get("use_llm") is not True or ctx.config.get("allow_llm_fallback") is True


def markdown_body(markdown: str) -> str:
    """Remove one leading Markdown heading to avoid nested sections."""

    lines = markdown.strip().splitlines()
    if lines and lines[0].lstrip().startswith("#"):
        lines = lines[1:]
        while lines and not lines[0].strip():
            lines = lines[1:]
    return "\n".join(lines).strip()


def read_jsonl_artifact(ctx: Context, filename: str) -> list[dict[str, Any]]:
    """Read a JSONL artifact when present, otherwise return an empty list."""

    path = ctx.find_artifact(filename)
    if path is None or not path.exists():
        return []
    try:
        return read_jsonl(path)
    except (OSError, json.JSONDecodeError):
        return []


def relative_artifact(ctx: Context, path: Path) -> str:
    """Render a path relative to the run directory when possible."""

    try:
        return str(path.relative_to(ctx.run_dir)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def safe_read_artifact(ctx: Context, filename: str) -> str:
    path = ctx.find_artifact(filename)
    return read_text(path) if path is not None else ""


def safe_read_json_artifact(ctx: Context, filename: str) -> dict[str, Any]:
    path = ctx.find_artifact(filename)
    if path is None:
        return {}
    data = read_json(path)
    return data if isinstance(data, dict) else {}


def string_items(value: object, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    rows: list[str] = []
    for item in value:
        text = str(item).strip()
        if text:
            rows.append(text)
        if len(rows) >= limit:
            break
    return rows


def string_sequence(value: object) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def text_field(data: dict[str, object], key: str) -> str:
    value = data.get(key)
    return value.strip() if isinstance(value, str) else ""


__all__ = [
    "ensure_heading",
    "handle_llm_failure",
    "list_value",
    "llm_client",
    "llm_fallback_allowed",
    "markdown_body",
    "read_jsonl_artifact",
    "relative_artifact",
    "safe_read_artifact",
    "safe_read_json_artifact",
    "string_items",
    "string_sequence",
    "text_field",
]
