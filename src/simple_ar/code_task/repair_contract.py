from __future__ import annotations

"""Shared repair-plan and atomic-patch metadata.

This module does not apply edits.  It turns existing repair decisions and edit
application results into stable artifacts that planning, review, repair, and
post-hoc analysis can all understand.
"""

import hashlib
import json
from pathlib import PurePosixPath
from typing import Any, Mapping


REPAIR_PLAN_SCHEMA_VERSION = "code_task_repair_plan.v1"
ATOMIC_PATCH_SET_SCHEMA_VERSION = "code_task_atomic_patch_set.v1"


def normalize_repair_plan(
    raw: Mapping[str, Any] | None,
    *,
    failure_analysis: Mapping[str, Any] | None = None,
    fallback_targets: list[str] | None = None,
    contract: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a generic RepairPlan artifact from an LLM or deterministic plan."""

    data = dict(raw or {})
    failure = dict(failure_analysis or {})
    target_rows = _target_file_rows(data.get("target_files"), fallback_targets or [])
    observed_error = _first_string(data.get("observed_error"), failure.get("summary"), failure.get("status"))
    root_cause = _first_string(data.get("root_cause"), data.get("diagnosis"), observed_error)
    affected = _affected_contracts(data, failure=failure, contract=contract or {}, target_rows=target_rows)
    return {
        "schema_version": REPAIR_PLAN_SCHEMA_VERSION,
        "failure_signature": _failure_signature(observed_error, root_cause, failure),
        "failure_kind": _first_string(data.get("failure_kind"), failure.get("status"), "runtime_failure"),
        "observed_error": observed_error,
        "root_cause": root_cause,
        "confidence": _confidence(data.get("confidence")),
        "affected_contracts": affected,
        "target_files": target_rows,
        "atomic_groups": _atomic_groups(data.get("atomic_groups"), target_rows),
        "validation_goals": _string_list(data.get("validation_goals")) or _default_validation_goals(affected),
        "do_not_repeat": _string_list(data.get("do_not_repeat")) or _string_list(data.get("previous_attempt_summary")),
        "repair_scope": _repair_scope(data.get("repair_scope"), target_rows),
        "repair_strategy": _first_string(data.get("repair_strategy"), data.get("strategy")),
        "risks": _string_list(data.get("risks")),
        "raw_plan_keys": sorted(str(key) for key in data.keys())[:80],
    }


def atomic_patch_set_record(
    *,
    repair_plan: Mapping[str, Any],
    applied_records: list[Mapping[str, Any]] | None = None,
    snapshot: Mapping[str, Any] | None = None,
    post_validation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a compact AtomicPatchSet artifact from applied repair records."""

    rows = [dict(row) for row in applied_records or [] if isinstance(row, Mapping)]
    actions: list[dict[str, Any]] = []
    for row in rows:
        app = row.get("edit_application")
        if not isinstance(app, Mapping):
            continue
        for action in app.get("applied_actions") if isinstance(app.get("applied_actions"), list) else []:
            if isinstance(action, Mapping):
                actions.append(_compact_action(action))
        for action in app.get("rejected_actions") if isinstance(app.get("rejected_actions"), list) else []:
            if isinstance(action, Mapping):
                rejected = _compact_action(action)
                rejected["rejected"] = True
                actions.append(rejected)
    changed = list(dict.fromkeys(str(row.get("path")) for row in rows if row.get("path")))
    return {
        "schema_version": ATOMIC_PATCH_SET_SCHEMA_VERSION,
        "repair_plan_signature": repair_plan.get("failure_signature", ""),
        "status": "patched" if changed else "skipped",
        "changed_files": changed,
        "action_count": len(actions),
        "actions": actions[:120],
        "snapshot": dict(snapshot or {}),
        "post_validation": dict(post_validation or {}),
    }


def _target_file_rows(value: Any, fallback: list[str]) -> list[dict[str, Any]]:
    rows = value if isinstance(value, list) else []
    result: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, Mapping):
            path = _safe_path(str(row.get("path") or row.get("file") or ""))
            rationale = _first_string(row.get("rationale"), row.get("reason"), row.get("why"))
            role = _first_string(row.get("role"), _path_role(path))
        else:
            path = _safe_path(str(row))
            rationale = ""
            role = _path_role(path)
        if path:
            result.append({"path": path, "role": role, "rationale": rationale})
    for path in fallback:
        safe = _safe_path(path)
        if safe and safe not in {row["path"] for row in result}:
            result.append({"path": safe, "role": _path_role(safe), "rationale": "fallback candidate"})
    return result[:20]


def _atomic_groups(value: Any, target_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if isinstance(value, list):
        groups = [dict(row) for row in value if isinstance(row, Mapping)]
        if groups:
            return groups[:20]
    paths = [row["path"] for row in target_rows]
    return [{"group_id": "repair-core", "files": paths, "reason": "files selected by repair diagnosis"}] if paths else []


def _affected_contracts(
    data: Mapping[str, Any],
    *,
    failure: Mapping[str, Any],
    contract: Mapping[str, Any],
    target_rows: list[dict[str, Any]],
) -> list[str]:
    explicit = _string_list(data.get("affected_contracts"))
    if explicit:
        return explicit
    text = json.dumps({"data": dict(data), "failure": dict(failure), "targets": target_rows}, ensure_ascii=False).lower()
    affected: list[str] = []
    probes = {
        "api": ("attribute", "import", "api", "function", "symbol"),
        "schema": ("field", "column", "key", "record", "dict", "mapping", "schema"),
        "metric": ("metric", "accuracy", "score", "f1", "rmse", "auc"),
        "artifact": ("readme", "result", "artifact", "output", "submission"),
        "resource": ("timeout", "iteration", "warning", "memory", "resource"),
        "entrypoint": ("main.py", "entrypoint", "traceback", "stderr"),
    }
    for name, terms in probes.items():
        if any(term in text for term in terms):
            affected.append(name)
    metric_contract = contract.get("metric_contract") if isinstance(contract.get("metric_contract"), Mapping) else {}
    if metric_contract.get("required_metrics") and "metric" not in affected:
        affected.append("metric")
    return affected or ["runtime"]


def _default_validation_goals(affected: list[str]) -> list[str]:
    goals = ["python_compile", "benchmark_command"]
    if "api" in affected or "schema" in affected:
        goals.append("local_api_contract")
    if "metric" in affected:
        goals.append("required_metrics_present")
    if "artifact" in affected:
        goals.append("required_artifacts_present")
    return goals


def _repair_scope(value: Any, target_rows: list[dict[str, Any]]) -> str:
    text = str(value or "").strip().lower()
    if text in {"block", "function", "file", "multi_file", "regenerate_plan"}:
        return text
    return "multi_file" if len(target_rows) > 1 else "file"


def _compact_action(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "action": _first_string(row.get("action")),
        "path": _safe_path(str(row.get("path") or "")),
        "public_api_changed": bool(row.get("public_api_changed")),
        "reason": _first_string(row.get("reason"), row.get("rationale"))[:300],
    }


def _failure_signature(*parts: Any) -> str:
    text = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _safe_path(value: str) -> str:
    text = str(value).replace("\\", "/").strip().lstrip("/")
    path = PurePosixPath(text)
    if not text or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return ""
    return path.as_posix()


def _path_role(path: str) -> str:
    lowered = path.lower()
    if lowered.endswith("main.py") or "entry" in lowered or "cli" in lowered:
        return "entrypoint"
    if any(term in lowered for term in ("runner", "experiment", "workflow", "pipeline")):
        return "orchestrator"
    if any(term in lowered for term in ("data", "input", "dataset", "source")):
        return "producer"
    if any(term in lowered for term in ("metric", "analysis", "report", "result", "artifact")):
        return "consumer"
    if any(term in lowered for term in ("model", "core", "algorithm")):
        return "core"
    return "support"


def _confidence(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in {"high", "medium", "low"} else "medium"


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _first_string(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""
