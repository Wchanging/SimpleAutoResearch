from __future__ import annotations

import re
from pathlib import Path

from simple_ar.core.artifacts import read_json, read_text

BASELINE_POLICIES = {"auto", "run", "skip", "provided", "none"}


def normalize_baseline_policy(value: str | None) -> str:
    text = str(value or "auto").strip().lower().replace("_", "-")
    aliases = {
        "default": "auto",
        "always": "run",
        "required": "run",
        "off": "skip",
        "false": "skip",
        "no": "skip",
        "external": "provided",
        "user": "provided",
        "manual": "provided",
    }
    normalized = aliases.get(text, text)
    if normalized not in BASELINE_POLICIES:
        raise ValueError("baseline_policy must be one of: " + ", ".join(sorted(BASELINE_POLICIES)))
    return normalized


def load_provided_baseline_metrics(
    run_dir: Path,
    baseline_metrics_file: str | Path | None,
    *,
    missing_message: str = "baseline_policy=provided requires a baseline metrics file.",
) -> tuple[dict[str, float], str]:
    if baseline_metrics_file is None or not str(baseline_metrics_file).strip():
        raise RuntimeError(missing_message)
    path = _resolve_user_file(run_dir, baseline_metrics_file)
    if not path.is_file():
        raise RuntimeError(f"Provided baseline metrics file does not exist: {path}")
    if path.suffix.lower() == ".json":
        metrics = _numeric_metric_dict(read_json(path))
    else:
        metrics = _parse_metric_text(read_text(path))
    if not metrics:
        raise RuntimeError(
            "Provided baseline metrics file must contain numeric metrics, "
            'for example {"accuracy": 0.82} or lines like accuracy=0.82.'
        )
    return metrics, path.as_posix()


def _resolve_user_file(run_dir: Path, value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    run_relative = run_dir / path
    if run_relative.exists():
        return run_relative
    return Path.cwd() / path


def _numeric_metric_dict(data: object) -> dict[str, float]:
    source = data
    if isinstance(data, dict) and isinstance(data.get("metric_values"), dict):
        source = data.get("metric_values")
    elif isinstance(data, dict) and isinstance(data.get("metrics"), dict):
        source = data.get("metrics")
    if not isinstance(source, dict):
        return {}
    return {
        str(name): float(value)
        for name, value in source.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    }


def _parse_metric_text(text: str) -> dict[str, float]:
    metrics: dict[str, float] = {}
    for line in text.splitlines():
        match = re.match(r"\s*([A-Za-z_][A-Za-z0-9_.:-]*)\s*[:=]\s*(-?\d+(?:\.\d+)?)\s*$", line)
        if match:
            metrics[match.group(1)] = float(match.group(2))
    return metrics
