from __future__ import annotations

from typing import Any

from simple_ar.artifacts import read_json
from simple_ar.pipeline import Context
from simple_ar.stages import Stage


def load_experiment_plan(ctx: Context) -> dict[str, Any]:
    path = None
    if ctx.state is not None and ctx.state.design.experiment_plan_path:
        path = ctx.resolve_artifact(ctx.state.design.experiment_plan_path)
    if path is None:
        path = ctx.artifact_path("experiment_plan.json", Stage.DESIGN)
    if path is None or not path.exists():
        return {}
    data = read_json(path)
    return data if isinstance(data, dict) else {}


def load_experiment_script_path(ctx: Context) -> str:
    path = None
    if ctx.state is not None and ctx.state.code.experiment_path:
        path = ctx.resolve_artifact(ctx.state.code.experiment_path)
    if path is None:
        path = ctx.artifact_path("experiment.py", Stage.CODE)
    if not path.exists():
        raise FileNotFoundError("experiment.py was not found")
    return str(path)
