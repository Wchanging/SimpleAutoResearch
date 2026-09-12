from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from simple_ar.core.artifacts import append_jsonl, read_jsonl, write_json
from simple_ar.integrations.llm import LLMUsage


def record_usage(
    meta_dir: Path,
    usage: LLMUsage,
    *,
    stage: str,
    batch_dir: Path | None = None,
    message_callback: Callable[[str], None] | None = None,
) -> None:
    """Write usage observations and display summaries, not budget settlements."""
    row = {**usage.to_row(), "stage": stage}
    usage_path = meta_dir / "llm_usage.jsonl"
    append_jsonl(usage_path, row)
    write_json(meta_dir / "llm_usage_summary.json", summarize_usage(read_jsonl(usage_path)))
    if batch_dir is not None:
        batch_path = batch_dir / "usage.jsonl"
        append_jsonl(batch_path, row)
        write_json(batch_dir / "usage_summary.json", summarize_usage(read_jsonl(batch_path)))
    if message_callback is not None:
        cost = usage.estimated_cost_usd
        cost_text = f", est cost ${cost:.6f}" if cost is not None else ""
        message_callback(
            f"LLM usage {row.get('label', '')}: "
            f"{usage.prompt_tokens} input + {usage.completion_tokens} output = "
            f"{usage.total_tokens} tokens ({usage.source}{cost_text})."
        )





def summarize_usage(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate LLM token records for a run.

    Args:
        records: Rows read from ``llm_usage.jsonl``.

    Returns:
        JSON-serializable summary with token totals and optional cost totals.
    """
    prompt_tokens = sum(_int_value(row.get("prompt_tokens")) for row in records)
    completion_tokens = sum(_int_value(row.get("completion_tokens")) for row in records)
    total_tokens = sum(_int_value(row.get("total_tokens")) for row in records)
    provider_attempts = sum(
        max(1, _int_value(row.get("provider_attempts")) or 1)
        for row in records
    )
    costs = [
        float(row["estimated_cost_usd"])
        for row in records
        if isinstance(row.get("estimated_cost_usd"), (int, float))
    ]
    cost_total = round(sum(costs), 8) if len(costs) == len(records) and records else None
    by_stage: dict[str, int] = {}
    for row in records:
        stage = str(row.get("stage", "unknown"))
        by_stage[stage] = by_stage.get(stage, 0) + _int_value(row.get("total_tokens"))

    return {
        "requests": len(records),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "provider_attempts": provider_attempts,
        "retry_count": max(0, provider_attempts - len(records)),
        "estimated_cost_usd": cost_total,
        "by_stage_total_tokens": by_stage,
    }




def _int_value(value: object) -> int:
    return value if isinstance(value, int) else 0
