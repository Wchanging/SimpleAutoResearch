"""Read-only view separating declarations from executor observations.

An observed argv proves invocation, not that a method was implemented correctly.
Writing, revision and both review levels consume this same projection.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from simple_ar.report.schema import ReportContext


def execution_record(result: Mapping[str, Any]) -> dict[str, Any]:
    execution = result.get("execution")
    process = execution.get("process") if isinstance(execution, Mapping) else None
    if not isinstance(process, Mapping) or not process.get("invocation_id"):
        return {"observation_status": "not_recorded"}
    # Legacy top-level commands are not independent executor observations.
    record = {key: process[key] for key in (
        "invocation_id", "status", "argv", "cwd", "started_at", "finished_at",
        "timeout_sec", "duration_sec", "returncode", "stop_reason",
    ) if key in process}
    argv = record.get("argv")
    if isinstance(argv, list):
        record["argv"] = [str(value)[:1000] for value in argv[:64]]
        record["argv_truncated"] = len(argv) > 64 or any(len(str(value)) > 1000 for value in argv)
    record["observation_status"] = "executor_recorded"
    streams = process.get("streams")
    if isinstance(streams, Mapping):
        record["streams"] = {
            name: {key: value[key] for key in (
                "log_path", "bytes_seen", "log_bytes_discarded", "tail_bytes_discarded",
            ) if key in value}
            for name, value in streams.items() if isinstance(value, Mapping)
        }
    return record


def report_execution_evidence(context: ReportContext) -> dict[str, Any]:
    """Preserve factual roles without loading files or rerunning an experiment."""
    plan = context.experiment_plan
    declared = {key: plan[key] for key in (
        "contract_id", "hypothesis", "dataset", "dataset_refs", "split_spec",
        "comparison_conditions", "metric_specs", "metrics", "expected_outcome",
        "resource_budget", "risks",
    ) if key in plan}
    results = context.results
    candidates: list[tuple[str, Mapping[str, Any]]] = [("current", results)]
    candidates.extend((role, results[role]) for role in ("baseline", "patched", "candidate")
                      if isinstance(results.get(role), Mapping))
    history = results.get("measurement_history")
    if isinstance(history, list):
        candidates.extend((str(row.get("action") or "history"), row)
                          for row in history if isinstance(row, Mapping))
    records, seen = [], set()
    for role, row in candidates:
        record = row.get("execution_record")
        record = dict(record) if isinstance(record, Mapping) else execution_record(row)
        identity = record.get("invocation_id")
        if not identity or identity in seen:
            continue
        seen.add(identity)
        records.append({"role": role, "artifact": str(row.get("artifact") or ""), **record})
    total = len(records)
    records = records if total <= 24 else [*records[:8], *records[-16:]]
    implementation = results.get("implementation")
    verification = implementation.get("method_validation") if isinstance(implementation, Mapping) else None
    return {
        "declared_protocol": declared,
        "declared_execution_context": {
            "text": context.execution_context[:5000],
            "total_characters": len(context.execution_context),
            "truncated": len(context.execution_context) > 5000,
        },
        "execution_records": records,
        "execution_records_total": total,
        "execution_records_omitted": total - len(records),
        "implementation_verification": dict(verification) if isinstance(verification, Mapping) else {
            "status": "not_checked", "reason": "No independent method verification was supplied.",
        },
        "interpretation_boundaries": [
            "Declared protocol/context describes requested or configured conditions, not independently verified behavior.",
            "Executor records prove invocation and recorded completion, not dataset contents, algorithm correctness or unrecorded hardware.",
            "A timeout is a configured limit; duration_sec is the observed elapsed time.",
            "A passed run and correct metric values do not independently validate the implementation mechanism.",
        ],
    }
