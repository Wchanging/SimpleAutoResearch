"""Canonical experiment result normalization."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from simple_ar.core.artifacts import read_json, write_json


CANONICAL_RESULT_SCHEMA_VERSION = "2.5"


def build_canonical_results(
    run_result: Any,
    *,
    result_schema: Mapping[str, Any] | None = None,
    experiment_contract: Mapping[str, Any] | None = None,
    artifacts: Mapping[str, Any] | None = None,
    comparisons: list[Mapping[str, Any]] | None = None,
    verdicts: list[Mapping[str, Any]] | None = None,
    guard: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a JSON-stable result shape while preserving legacy keys."""

    metrics = _mapping(getattr(run_result, "metrics", {}))
    command = list(getattr(run_result, "command", []) or [])
    returncode = getattr(run_result, "returncode", None)
    timed_out = bool(getattr(run_result, "timed_out", False))
    status = _status(returncode, timed_out)
    stdout = str(getattr(run_result, "stdout", "") or "")
    stderr = str(getattr(run_result, "stderr", "") or "")
    schema = dict(result_schema or {})
    primary_metric = str(schema.get("primary_metric") or _first_metric(metrics))
    canonical: dict[str, Any] = {
        "schema_version": CANONICAL_RESULT_SCHEMA_VERSION,
        "generated_at": _utcnow_iso(),
        "status": status,
        # Legacy top-level keys kept for report/context compatibility.
        "returncode": returncode,
        "timed_out": timed_out,
        "metrics": dict(metrics),
        "command": command,
        # Canonical execution record.
        "execution": {
            "backend": str(getattr(run_result, "backend", "local") or "local"),
            "label": str(getattr(run_result, "label", "experiment") or "experiment"),
            "cwd": str(getattr(run_result, "cwd", "") or ""),
            "duration_sec": float(getattr(run_result, "duration_sec", 0.0) or 0.0),
            "stdout_chars": len(stdout),
            "stderr_chars": len(stderr),
        },
        "result_schema": schema,
        "primary_metric": primary_metric,
        "artifacts": dict(artifacts or {}),
        "comparisons": [dict(item) for item in comparisons or []],
        "verdicts": [dict(item) for item in verdicts or []],
    }
    if experiment_contract:
        canonical["experiment_contract"] = dict(experiment_contract)
    if guard:
        canonical["guard"] = dict(guard)
        if str(guard.get("status", "")).lower() == "failed":
            canonical["status"] = "failed"
    return canonical


def write_canonical_results(path: Path, results: Mapping[str, Any]) -> None:
    write_json(path, dict(results))


def load_optional_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = read_json(path)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _status(returncode: object, timed_out: bool) -> str:
    if timed_out:
        return "timed_out"
    return "passed" if returncode == 0 else "failed"


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _first_metric(metrics: Mapping[str, Any]) -> str:
    for key in metrics:
        return str(key)
    return ""


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
