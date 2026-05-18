from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from simple_ar.artifacts import read_json, write_json
from simple_ar.code_task.state import (
    code_task_paths,
    load_code_task_manifest,
    manifest_section,
    save_code_task_manifest,
    utcnow_iso,
)


HIGHER_IS_BETTER_HINTS = (
    "accuracy",
    "auc",
    "f1",
    "precision",
    "recall",
    "score",
    "success",
)
LOWER_IS_BETTER_HINTS = (
    "error",
    "loss",
)
RESOURCE_HINTS = (
    "cost",
    "duration",
    "latency",
    "memory",
    "ms",
    "parameter",
    "params",
    "sec",
    "seconds",
    "time",
)
DEFAULT_TOLERANCE = 1e-9
DIRECTION_ALIASES = {
    "higher": "higher_is_better",
    "higher_is_better": "higher_is_better",
    "maximize": "higher_is_better",
    "max": "higher_is_better",
    "lower": "lower_is_better",
    "lower_is_better": "lower_is_better",
    "minimize": "lower_is_better",
    "min": "lower_is_better",
    "resource": "resource",
    "cost": "resource",
    "ignore": "ignore",
    "ignored": "ignore",
    "neutral": "ignore",
}


@dataclass(frozen=True)
class CodeTaskComparisonResult:
    """Result returned after comparing baseline and patched benchmark runs."""

    run_dir: Path
    comparison_path: Path
    verdict: str
    deltas: dict[str, float]


def compare_code_task_runs(run_dir: Path) -> CodeTaskComparisonResult:
    """Compare baseline and patched benchmark artifacts for a code-task run.

    The comparison is intentionally conservative. It records metric deltas and
    a lightweight verdict, but it does not claim broad scientific significance.
    """
    manifest = load_code_task_manifest(run_dir)
    paths = code_task_paths(run_dir)
    comparison_path = paths.run_artifact_dir / "comparison.json"
    baseline = _read_run(paths.run_artifact_dir, "baseline")
    patched = _read_run(paths.run_artifact_dir, "patched")
    metric_config = _metric_config(manifest)
    comparison = _build_comparison(
        baseline=baseline,
        patched=patched,
        metric_config=metric_config,
    )
    write_json(comparison_path, comparison)
    _update_manifest_after_comparison(run_dir, manifest, comparison)
    return CodeTaskComparisonResult(
        run_dir=paths.run_dir,
        comparison_path=comparison_path,
        verdict=str(comparison.get("verdict", "inconclusive")),
        deltas={
            str(key): float(value)
            for key, value in comparison.get("deltas", {}).items()
            if isinstance(value, (int, float))
        },
    )


def _read_run(run_artifact_dir: Path, label: str) -> dict[str, Any]:
    execution_path = run_artifact_dir / label / "execution_report.json"
    metrics_path = run_artifact_dir / label / "metrics.json"
    if not execution_path.exists():
        return {}
    execution = read_json(execution_path)
    metrics = read_json(metrics_path) if metrics_path.exists() else {}
    return {
        "label": label,
        "execution": execution if isinstance(execution, dict) else {},
        "metrics": metrics if isinstance(metrics, dict) else {},
    }


def _build_comparison(
    *,
    baseline: dict[str, Any],
    patched: dict[str, Any],
    metric_config: dict[str, Any],
) -> dict[str, Any]:
    baseline_execution = _dict_value(baseline.get("execution"))
    patched_execution = _dict_value(patched.get("execution"))
    baseline_metrics = _numeric_metrics(_dict_value(baseline.get("metrics")))
    patched_metrics = _numeric_metrics(_dict_value(patched.get("metrics")))
    metric_names = sorted(set(baseline_metrics) & set(patched_metrics))
    metric_rows = [
        _metric_row(name, baseline_metrics[name], patched_metrics[name], metric_config)
        for name in metric_names
    ]
    verdict, reasons = _verdict(
        baseline_execution=baseline_execution,
        patched_execution=patched_execution,
        metric_rows=metric_rows,
        primary_metric=_primary_metric(metric_config),
    )
    return {
        "schema_version": 1,
        "generated_at": utcnow_iso(),
        "status": "ready" if baseline and patched else "incomplete",
        "verdict": verdict,
        "reasons": reasons,
        "metric_config": metric_config,
        "baseline": _run_summary(baseline_execution, baseline_metrics),
        "patched": _run_summary(patched_execution, patched_metrics),
        "metrics": metric_rows,
        "deltas": {row["name"]: row["delta"] for row in metric_rows},
    }


def _metric_row(
    name: str,
    baseline: float,
    patched: float,
    metric_config: dict[str, Any],
) -> dict[str, Any]:
    delta = patched - baseline
    direction, direction_source = _metric_direction(name, metric_config)
    if abs(delta) <= DEFAULT_TOLERANCE:
        interpretation = "unchanged"
    elif direction == "higher_is_better":
        interpretation = "improved" if delta > 0 else "regressed"
    elif direction == "lower_is_better":
        interpretation = "improved" if delta < 0 else "regressed"
    elif direction == "resource":
        interpretation = "decreased" if delta < 0 else "increased"
    elif direction == "ignore":
        interpretation = "ignored"
    else:
        interpretation = "changed"
    primary_metric = _primary_metric(metric_config)
    return {
        "name": name,
        "baseline": baseline,
        "patched": patched,
        "delta": delta,
        "direction": direction,
        "direction_source": direction_source,
        "interpretation": interpretation,
        "is_primary": _same_metric(name, primary_metric) if primary_metric else False,
    }


def _metric_direction(name: str, metric_config: dict[str, Any]) -> tuple[str, str]:
    configured = _configured_metric_direction(name, metric_config)
    if configured:
        return configured, "configured"
    lowered = name.lower()
    if any(hint in lowered for hint in RESOURCE_HINTS):
        return "resource", "heuristic"
    if any(hint in lowered for hint in HIGHER_IS_BETTER_HINTS):
        return "higher_is_better", "heuristic"
    if any(hint in lowered for hint in LOWER_IS_BETTER_HINTS):
        return "lower_is_better", "heuristic"
    return "unknown", "none"


def _verdict(
    *,
    baseline_execution: dict[str, Any],
    patched_execution: dict[str, Any],
    metric_rows: list[dict[str, Any]],
    primary_metric: str,
) -> tuple[str, list[str]]:
    baseline_status = str(baseline_execution.get("status", "missing"))
    patched_status = str(patched_execution.get("status", "missing"))
    if not baseline_execution or not patched_execution:
        return "inconclusive", ["baseline or patched execution artifact is missing"]
    if baseline_status != "passed" and patched_status == "passed":
        return "improved", [f"patched run passed after baseline status `{baseline_status}`"]
    if baseline_status == "passed" and patched_status != "passed":
        return "regressed", [f"patched run status `{patched_status}` after passing baseline"]
    if patched_status != "passed":
        return "inconclusive", [
            "both runs are non-passing or blocked: "
            f"baseline={baseline_status}, patched={patched_status}"
        ]

    directional = _verdict_metric_rows(metric_rows, primary_metric=primary_metric)
    if primary_metric and not directional:
        primary_rows = [
            row
            for row in metric_rows
            if _same_metric(str(row.get("name", "")), primary_metric)
        ]
        if not primary_rows:
            return "inconclusive", [
                f"configured primary metric `{primary_metric}` was not shared by both runs"
            ]
        return "inconclusive", [
            f"configured primary metric `{primary_metric}` has direction "
            f"`{primary_rows[0].get('direction', 'unknown')}`, so it cannot determine improvement"
        ]
    if not directional:
        return "inconclusive", ["no directional numeric metrics were shared by both runs"]

    improved = [row for row in directional if row.get("interpretation") == "improved"]
    regressed = [row for row in directional if row.get("interpretation") == "regressed"]
    if improved and not regressed:
        return "improved", _metric_reasons(improved, prefix="improved")
    if regressed and not improved:
        return "regressed", _metric_reasons(regressed, prefix="regressed")
    if improved and regressed:
        return "mixed", _metric_reasons(improved, prefix="improved") + _metric_reasons(
            regressed,
            prefix="regressed",
        )
    return "unchanged", ["directional metrics were unchanged within tolerance"]


def _verdict_metric_rows(
    metric_rows: list[dict[str, Any]],
    *,
    primary_metric: str,
) -> list[dict[str, Any]]:
    rows = [
        row
        for row in metric_rows
        if row.get("direction") in {"higher_is_better", "lower_is_better"}
    ]
    if not primary_metric:
        return rows
    return [
        row
        for row in rows
        if _same_metric(str(row.get("name", "")), primary_metric)
    ]


def _metric_reasons(rows: list[dict[str, Any]], *, prefix: str) -> list[str]:
    return [
        f"{prefix} `{row['name']}` by {_format_delta(float(row['delta']))}"
        for row in rows[:6]
    ]


def _run_summary(execution: dict[str, Any], metrics: dict[str, float]) -> dict[str, Any]:
    return {
        "status": execution.get("status", "missing"),
        "returncode": execution.get("returncode"),
        "timed_out": execution.get("timed_out"),
        "duration_sec": execution.get("duration_sec"),
        "metrics": metrics,
    }


def _numeric_metrics(value: dict[str, Any]) -> dict[str, float]:
    metrics: dict[str, float] = {}
    for key, raw in value.items():
        if isinstance(raw, bool):
            continue
        if isinstance(raw, (int, float)):
            metrics[str(key)] = float(raw)
    return metrics


def normalize_metric_directions(
    value: Mapping[str, str] | None,
) -> dict[str, str]:
    """Normalize user-provided metric direction configuration.

    Unknown directions are rejected early so comparison artifacts remain
    deterministic and easy to inspect.
    """
    directions: dict[str, str] = {}
    for raw_name, raw_direction in (value or {}).items():
        name = str(raw_name).strip()
        if not name:
            continue
        direction = normalize_metric_direction(raw_direction)
        directions[name] = direction
    return directions


def normalize_metric_direction(value: str) -> str:
    """Normalize one metric direction alias."""
    key = str(value).strip().lower().replace("-", "_")
    direction = DIRECTION_ALIASES.get(key)
    if not direction:
        allowed = ", ".join(sorted(DIRECTION_ALIASES))
        raise ValueError(
            f"Unknown metric direction `{value}`. Allowed aliases: {allowed}"
        )
    return direction


def _metric_config(manifest: dict[str, Any]) -> dict[str, Any]:
    benchmark = _dict_value(manifest.get("benchmark"))
    primary_metric = str(benchmark.get("primary_metric") or "").strip()
    raw_directions = benchmark.get("metric_directions")
    directions: dict[str, str] = {}
    warnings: list[str] = []
    if isinstance(raw_directions, dict):
        for name, raw_direction in raw_directions.items():
            metric_name = str(name).strip()
            if not metric_name:
                continue
            try:
                directions[metric_name] = normalize_metric_direction(str(raw_direction))
            except ValueError as exc:
                warnings.append(str(exc))
    return {
        "primary_metric": primary_metric,
        "metric_directions": directions,
        "warnings": warnings,
    }


def _primary_metric(metric_config: dict[str, Any]) -> str:
    return str(metric_config.get("primary_metric") or "").strip()


def _configured_metric_direction(
    name: str,
    metric_config: dict[str, Any],
) -> str:
    directions = _dict_value(metric_config.get("metric_directions"))
    if name in directions:
        return str(directions[name])
    lowered = name.lower()
    for key, value in directions.items():
        if str(key).lower() == lowered:
            return str(value)
    return ""


def _same_metric(left: str, right: str) -> bool:
    return left.strip().lower() == right.strip().lower()


def _dict_value(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _format_delta(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.6g}"


def _update_manifest_after_comparison(
    run_dir: Path,
    manifest: dict[str, Any],
    comparison: dict[str, Any],
) -> None:
    layout = manifest_section(manifest, "layout")
    layout["comparison"] = "code_task/run/comparison.json"
    benchmark = manifest_section(manifest, "benchmark")
    benchmark["comparison"] = {
        "path": "code_task/run/comparison.json",
        "verdict": comparison.get("verdict", "inconclusive"),
        "generated_at": comparison.get("generated_at"),
        "deltas": comparison.get("deltas", {}),
        "metric_config": comparison.get("metric_config", {}),
    }
    manifest["layout"] = layout
    manifest["benchmark"] = benchmark
    save_code_task_manifest(run_dir, manifest)
