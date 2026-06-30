from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from simple_ar.code_task.generation.common import string_list


def initial_implementation_memory(
    *,
    contract: Mapping[str, Any],
    architecture_plan: Mapping[str, Any],
    mode: str,
) -> dict[str, Any]:
    return {
        "schema_version": "implementation_memory.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": mode,
        "task": {
            "contract_id": contract.get("contract_id", ""),
            "task_kind": contract.get("task_kind", ""),
            "objective": contract.get("objective", ""),
            "explicit_requirements": string_list(contract.get("explicit_requirements"), limit=30),
            "deliverables": string_list(contract.get("deliverables"), limit=20),
            "constraints": string_list(contract.get("constraints"), limit=20),
            "evaluation_focus": string_list(contract.get("evaluation_focus"), limit=20),
            "evidence_plan": dict(contract.get("evidence_plan", {})) if isinstance(contract.get("evidence_plan"), Mapping) else {},
            "metric_contract": dict(contract.get("metric_contract", {})) if isinstance(contract.get("metric_contract"), Mapping) else {},
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

