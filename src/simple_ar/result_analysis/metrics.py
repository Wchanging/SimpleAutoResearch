from __future__ import annotations

import math
from typing import Any

from .schema import AnalysisContext, MetricDirection


def build_metric_summary(context: AnalysisContext) -> dict[str, Any]:
    metrics = dict(context.metrics)
    expected = expected_metric_names(context)
    directions = metric_directions(context, expected)
    details: list[dict[str, Any]] = []
    weak_signals: list[str] = []

    for name in sorted(set(metrics) | set(expected)):
        value = metrics.get(name)
        issues: list[str] = []
        if value is None:
            issues.append("missing")
        elif not math.isfinite(float(value)):
            issues.append("non_finite")
        direction = directions.get(name, "unknown")
        if direction == "unknown" and value is not None:
            issues.append("unknown_direction")
        if issues:
            weak_signals.append(f"{name}: {', '.join(issues)}")
        details.append(
            {
                "name": name,
                "value": value,
                "direction": direction,
                "present": value is not None,
                "issues": issues,
            }
        )

    comparable = [
        float(value)
        for name, value in metrics.items()
        if isinstance(value, (int, float))
        and math.isfinite(float(value))
        and directions.get(name, "unknown") not in {"resource", "ignore"}
    ]
    if metrics and comparable and all(abs(value) < 1e-12 for value in comparable):
        weak_signals.append("all comparable metrics are zero")
    if not metrics:
        weak_signals.append("no numeric metrics found")

    missing_required = [name for name in expected if name not in metrics]
    return {
        "metric_count": len(metrics),
        "expected_metric_count": len(expected),
        "missing_required_metrics": missing_required,
        "weak_metric_signals": sorted(dict.fromkeys(weak_signals)),
        "metrics": details,
        "primary_metric": expected[0] if expected else next(iter(metrics), ""),
    }


def expected_metric_names(context: AnalysisContext) -> list[str]:
    names: list[str] = []
    for row in context.expected_metrics:
        if isinstance(row, dict) and row.get("name"):
            names.append(str(row["name"]))
    for name in context.metric_directions:
        if name not in names and context.metric_directions[name] != "ignore":
            names.append(name)
    return list(dict.fromkeys(names))


def metric_directions(context: AnalysisContext, expected: list[str] | None = None) -> dict[str, MetricDirection]:
    directions: dict[str, MetricDirection] = dict(context.metric_directions)
    for row in context.expected_metrics:
        if not isinstance(row, dict) or not row.get("name"):
            continue
        name = str(row["name"])
        raw = row.get("direction")
        if name not in directions:
            directions[name] = normalize_direction(raw)
    if expected:
        for name in expected:
            directions.setdefault(name, "unknown")
    return directions


def normalize_direction(value: Any) -> MetricDirection:
    text = str(value or "").strip().lower()
    if text in {"higher", "maximize", "max", "increase", "larger"}:
        return "higher"
    if text in {"lower", "minimize", "min", "decrease", "smaller"}:
        return "lower"
    if text in {"resource", "cost", "runtime", "latency", "memory"}:
        return "resource"
    if text in {"ignore", "none", "n/a"}:
        return "ignore"
    return "unknown"


def flatten_numeric_metrics(data: Any, *, prefix: str = "") -> dict[str, float]:
    out: dict[str, float] = {}
    if isinstance(data, dict):
        for key, value in data.items():
            name = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)) and math.isfinite(float(value)):
                out[name] = float(value)
            elif isinstance(value, dict):
                out.update(flatten_numeric_metrics(value, prefix=name))
    return out
