from __future__ import annotations

from typing import Any

from simple_ar.artifacts import append_jsonl, read_jsonl, write_json
from simple_ar.llm import LLMUsage
from simple_ar.pipeline import Context


def record_llm_usage(ctx: Context, usage: LLMUsage) -> None:
    """Persist one LLM usage record and update the run-level summary.

    Args:
        ctx: Current pipeline context.
        usage: Token usage emitted by ``LLMClient``.
    """
    usage_path = ctx.run_dir / "llm_usage.jsonl"
    row = usage.to_row()
    row["stage"] = ctx.current_stage.name.lower()
    append_jsonl(usage_path, row)
    records = read_jsonl(usage_path)
    summary = summarize_usage(records)
    write_json(ctx.run_dir / "llm_usage_summary.json", summary)
    ctx.emit(
        "llm_usage",
        _format_usage_message(row),
        prompt_tokens=row["prompt_tokens"],
        completion_tokens=row["completion_tokens"],
        total_tokens=row["total_tokens"],
        estimated_cost_usd=row["estimated_cost_usd"],
        source=row["source"],
        label=row["label"],
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
        "estimated_cost_usd": cost_total,
        "by_stage_total_tokens": by_stage,
    }


def _format_usage_message(row: dict[str, Any]) -> str:
    label = f" {row['label']}" if row.get("label") else ""
    cost = row.get("estimated_cost_usd")
    cost_text = f", est cost ${cost:.6f}" if isinstance(cost, (int, float)) else ""
    return (
        f"LLM usage{label}: "
        f"{row['prompt_tokens']} input + {row['completion_tokens']} output = "
        f"{row['total_tokens']} tokens ({row['source']}{cost_text})."
    )


def _int_value(value: object) -> int:
    return value if isinstance(value, int) else 0
