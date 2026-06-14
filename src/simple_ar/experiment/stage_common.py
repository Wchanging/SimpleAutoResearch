from __future__ import annotations

from pathlib import Path

from simple_ar.core.pipeline import Context
from simple_ar.experiment.coding import effective_experiment_template
from simple_ar.experiment.execution.results import load_optional_json


def design_json(ctx: Context, name: str) -> dict[str, object]:
    return load_optional_json(ctx.run_dir / "05-design" / name)


def experiment_template(ctx: Context) -> str:
    return effective_experiment_template(ctx.config)


def greenfield_metrics(ctx: Context) -> list[str]:
    required = ctx.config.get("evaluation_required_metrics")
    metrics = [str(item) for item in required if str(item).strip()] if isinstance(required, list) else []
    primary = str(ctx.config.get("evaluation_primary_metric") or "").strip()
    if primary and primary not in metrics:
        metrics.insert(0, primary)
    return metrics or ["score"]


def model_name(ctx: Context) -> str | None:
    model_value = ctx.config.get("model")
    return str(model_value) if model_value else None


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def experiment_timeout(ctx: Context) -> int:
    value = ctx.config.get("experiment_timeout_sec", 30)
    try:
        timeout = int(value)
    except (TypeError, ValueError):
        timeout = 30
    return min(max(1, timeout), 300)


def relative_or_string(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)
