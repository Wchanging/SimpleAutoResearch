from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from simple_ar.core.artifacts import read_jsonl
from simple_ar.integrations.llm import (
    LLMClient,
    LLMError,
)
from simple_ar.core.pipeline import Context
from simple_ar.research.outputs.artifacts import (
    SEARCH_CACHE_MANIFEST,
    SEARCH_FULLTEXT_MANIFEST,
    SEARCH_META,
    SEARCH_RESEARCH_PLAN,
)
from simple_ar.research.service import (
    safe_read_artifact,
    safe_read_json_artifact,
)
from simple_ar.retrieval.evidence import collect_stage_evidence
from simple_ar.app.usage import record_llm_usage

def _downstream_source_plan(ctx: Context) -> dict[str, Any]:
    """Return source-plan metadata for downstream stages after compaction.

    Verbose runs keep ``planning/research_plan.json``. Compact runs remove that
    debug artifact, so search also stores a compact source-plan copy in
    ``search_meta.json``. Older compact runs may lack that copy; for those we
    reconstruct the minimum reliable fields from retained search manifests.
    """

    research_plan = _safe_read_json_artifact(ctx, SEARCH_RESEARCH_PLAN)
    if isinstance(research_plan, dict):
        source_plan = research_plan.get("source_plan")
        if _usable_source_plan(source_plan):
            return dict(source_plan)

    search_meta = _safe_read_json_artifact(ctx, SEARCH_META)
    source_plan = search_meta.get("source_plan") if isinstance(search_meta, dict) else None
    if _usable_source_plan(source_plan):
        return dict(source_plan)

    return _source_plan_from_search_manifests(ctx, search_meta if isinstance(search_meta, dict) else {})

def _ensure_heading(markdown: str, heading: str) -> str:
    stripped = markdown.strip()
    if stripped.startswith("#"):
        return stripped + "\n"
    return f"# {heading}\n\n{stripped}\n"

def _list_value(value: object) -> list[Any]:
    return value if isinstance(value, list) else []

def _llm_client(ctx: Context) -> LLMClient | None:
    """Create an LLM client for a stage when LLM mode is enabled.

    Args:
        ctx: Current pipeline context containing runtime configuration.

    Returns:
        Configured client, or ``None`` when offline fallback should be used.
    """
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
        _handle_llm_failure(ctx, "LLM client initialization failed", exc)
        return None


def _llm_fallback_allowed(ctx: Context) -> bool:
    """Return whether a failed LLM operation may use deterministic output."""
    return ctx.config.get("use_llm") is not True or ctx.config.get("allow_llm_fallback") is True


def _handle_llm_failure(ctx: Context, message: str, exc: Exception) -> None:
    """Raise in strict online mode; otherwise announce the explicit fallback."""
    if not _llm_fallback_allowed(ctx):
        raise LLMError(
            f"{message}: {exc}. LLM fallback is disabled; retry the same run or use --no-llm explicitly."
        ) from exc
    ctx.emit("stage_message", f"{message}; using explicit offline fallback. {exc}")

def _markdown_body(markdown: str) -> str:
    """Remove one leading Markdown heading to avoid nested report sections."""
    lines = markdown.strip().splitlines()
    if lines and lines[0].lstrip().startswith("#"):
        lines = lines[1:]
        while lines and not lines[0].strip():
            lines = lines[1:]
    return "\n".join(lines).strip()

def _read_jsonl_artifact(ctx: Context, filename: str) -> list[dict[str, Any]]:
    """Read a JSONL artifact when present, otherwise return an empty list."""
    path = ctx.find_artifact(filename)
    if path is None or not path.exists():
        return []
    try:
        return read_jsonl(path)
    except (OSError, json.JSONDecodeError):
        return []

def _relative_artifact(ctx: Context, path: Path) -> str:
    """Render a path relative to the run directory when possible."""
    try:
        return str(path.relative_to(ctx.run_dir)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")

def _retrieval_top_k(ctx: Context) -> int:
    """Read the per-query retrieval result limit with a conservative default."""
    value = ctx.config.get("retrieval_top_k", 4)
    try:
        top_k = int(value)
    except (TypeError, ValueError):
        top_k = 4
    return min(max(1, top_k), 20)

def _safe_read_artifact(ctx: Context, filename: str) -> str:
    """Read an artifact when present, otherwise return an empty string."""
    return safe_read_artifact(ctx, filename)

def _safe_read_json_artifact(ctx: Context, filename: str) -> dict[str, Any]:
    """Read a JSON artifact as a dictionary when present."""
    return safe_read_json_artifact(ctx, filename)

def _source_plan_from_search_manifests(ctx: Context, search_meta: dict[str, Any]) -> dict[str, Any]:
    cache_manifest = _safe_read_json_artifact(ctx, SEARCH_CACHE_MANIFEST)
    fulltext_manifest = _safe_read_json_artifact(ctx, SEARCH_FULLTEXT_MANIFEST)
    fulltext_budget = fulltext_manifest.get("budget") if isinstance(fulltext_manifest, dict) else {}
    budget = dict(fulltext_budget) if isinstance(fulltext_budget, dict) else {}
    return {
        "schema_version": "source_plan.reconstructed.v1",
        "queries": _string_sequence(search_meta.get("queries")) or _string_sequence([search_meta.get("query")])
        or [ctx.topic],
        "sources": _string_sequence(search_meta.get("sources")) or _string_sequence(search_meta.get("sources_used")),
        "mode": str(ctx.config.get("research_mode") or "standard"),
        "require_fulltext": bool(cache_manifest.get("require_fulltext") or fulltext_manifest.get("enabled")),
        "allow_pdf_download": bool(cache_manifest.get("allow_pdf_download") or fulltext_manifest.get("allow_pdf_download")),
        "index_backend": str(search_meta.get("index_backend") or "keyword"),
        "budget": budget,
    }

def _stage_evidence(ctx: Context, stage: str) -> list[dict[str, Any]]:
    """Collect local retrieval evidence for a stage when enabled.

    Args:
        ctx: Current pipeline context.
        stage: Logical stage name used in ``source_plan.json``.

    Returns:
        Evidence rows with source paths and line ranges. Empty when retrieval is
        explicitly disabled.
    """
    if ctx.config.get("use_retrieval", True) is False:
        return []
    top_k = _retrieval_top_k(ctx)
    try:
        rows = collect_stage_evidence(ctx.run_dir, ctx.topic, stage, top_k=top_k)
        if rows:
            ctx.emit(
                "stage_message",
                f"Retrieved {len(rows)} source snippet(s) for {stage} evidence.",
            )
        return rows
    except Exception as exc:
        ctx.emit("stage_message", f"Retrieval evidence failed for {stage}; continuing. {exc}")
        return []

def _string_items(value: object, *, limit: int) -> list[str]:
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

def _string_sequence(value: object) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]

def _text_field(data: dict[str, object], key: str) -> str:
    value = data.get(key)
    return value.strip() if isinstance(value, str) else ""

def _usable_source_plan(value: object) -> bool:
    return isinstance(value, dict) and bool(value.get("queries") or value.get("sources"))

__all__ = [
    "_downstream_source_plan",
    "_ensure_heading",
    "_list_value",
    "_llm_client",
    "_markdown_body",
    "_read_jsonl_artifact",
    "_relative_artifact",
    "_retrieval_top_k",
    "_safe_read_artifact",
    "_safe_read_json_artifact",
    "_source_plan_from_search_manifests",
    "_stage_evidence",
    "_string_items",
    "_string_sequence",
    "_text_field",
    "_usable_source_plan",
]
