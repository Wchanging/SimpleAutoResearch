"""Compatibility-only helpers for the frozen eight-stage pipeline.

Generic artifact and LLM helpers live in ``simple_ar.core.runtime``. The
remaining functions here reconstruct legacy source-plan metadata and local
retrieval evidence needed only by the old stage layout.
"""

from __future__ import annotations

from typing import Any
from simple_ar.core.pipeline import Context
from simple_ar.core.runtime import (
    ensure_heading as _ensure_heading,
    handle_llm_failure as _handle_llm_failure,
    list_value as _list_value,
    llm_client as _llm_client,
    markdown_body as _markdown_body,
    read_jsonl_artifact as _read_jsonl_artifact,
    relative_artifact as _relative_artifact,
    safe_read_artifact as _safe_read_artifact,
    safe_read_json_artifact as _safe_read_json_artifact,
    string_items as _string_items,
    string_sequence as _string_sequence,
    text_field as _text_field,
)
from simple_ar.research.outputs.artifacts import (
    SEARCH_CACHE_MANIFEST,
    SEARCH_FULLTEXT_MANIFEST,
    SEARCH_META,
    SEARCH_RESEARCH_PLAN,
)
from simple_ar.retrieval.evidence import collect_stage_evidence

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

def _retrieval_top_k(ctx: Context) -> int:
    """Read the per-query retrieval result limit with a conservative default."""
    value = ctx.config.get("retrieval_top_k", 4)
    try:
        top_k = int(value)
    except (TypeError, ValueError):
        top_k = 4
    return min(max(1, top_k), 20)

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
