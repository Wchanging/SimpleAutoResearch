from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping


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
        },
        "accepted_decisions": [
            architecture_plan.get("architecture_summary", ""),
            "Generated code must print metric lines consumed by canonical results.",
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

