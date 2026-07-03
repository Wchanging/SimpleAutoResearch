from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from simple_ar.code_task.generation.common import string_list
from simple_ar.code_task.generation.task_contract import contract_prompt_view


def initial_implementation_memory(
    *,
    contract: Mapping[str, Any],
    architecture_plan: Mapping[str, Any],
    mode: str,
) -> dict[str, Any]:
    task_view = contract_prompt_view(
        contract,
        max_task_chars=900,
        max_requirements=18,
        max_success_criteria=12,
    )
    return {
        "schema_version": "implementation_memory.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": mode,
        "task": {
            "contract_id": task_view.get("contract_id", ""),
            "task_kind": task_view.get("task_kind", ""),
            "objective": task_view.get("objective", ""),
            "explicit_requirements": string_list(task_view.get("explicit_requirements"), limit=18),
            "deliverables": string_list(task_view.get("deliverables"), limit=10),
            "constraints": string_list(task_view.get("constraints"), limit=10),
            "evaluation_focus": string_list(task_view.get("evaluation_focus"), limit=12),
            "evidence_plan": _compact_evidence_plan(task_view.get("evidence_plan")),
            "metric_contract": dict(task_view.get("metric_contract", {}))
            if isinstance(task_view.get("metric_contract"), Mapping)
            else {},
        },
        "accepted_decisions": [
            architecture_plan.get("architecture_summary", ""),
            "Generated code must print metric lines consumed by canonical results.",
            "Generated artifacts must preserve the evidence needed to support or refute every task hypothesis.",
        ],
        "file_summaries": [],
        "generated_batches": [],
        "open_issues": [],
        "review_findings": [],
        "repair_history": [],
    }


def _compact_evidence_plan(value: object) -> dict[str, Any]:
    plan = value if isinstance(value, Mapping) else {}
    return {
        "schema_version": plan.get("schema_version", "code_task_evidence_plan.v1"),
        "hypotheses": string_list(plan.get("hypotheses"), limit=8),
        "required_conditions": string_list(plan.get("required_conditions"), limit=10),
        "required_datasets": string_list(plan.get("required_datasets"), limit=8),
        "required_metrics": string_list(plan.get("required_metrics"), limit=30),
        "required_artifacts": string_list(plan.get("required_artifacts"), limit=8),
        "required_comparisons": string_list(plan.get("required_comparisons"), limit=8),
        "primary_metric": plan.get("primary_metric", ""),
    }


def record_generated_file(
    memory: dict[str, Any],
    *,
    path: str,
    summary: str,
    mode: str,
    public_api: list[str] | None = None,
) -> None:
    memory.setdefault("file_summaries", []).append(
        {
            "path": path,
            "summary": summary,
            "mode": mode,
            "public_api": list(public_api or []),
        }
    )


def record_generation_batch(memory: dict[str, Any], *, batch_id: str, files: list[str], mode: str) -> None:
    memory.setdefault("generated_batches", []).append(
        {
            "batch_id": batch_id,
            "files": files,
            "mode": mode,
        }
    )

