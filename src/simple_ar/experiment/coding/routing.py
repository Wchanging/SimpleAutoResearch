from __future__ import annotations

from typing import Any, Mapping

from simple_ar.experiment.code_task_bridge import is_code_task_experiment_template
from simple_ar.code_task.generation.architecture import GREENFIELD_TEMPLATE


def implementation_route(config: Mapping[str, Any], plan: Mapping[str, Any]) -> str:
    """Return `code_task`, `greenfield`, or `template` for the code stage."""

    template = str(plan.get("template") or config.get("experiment_template") or "").strip()
    if is_code_task_experiment_template(template):
        return "code_task"
    mode = str(config.get("implementation_mode") or "").strip().lower()
    kind = str(config.get("task_kind") or "").strip().lower()
    if template == GREENFIELD_TEMPLATE or mode == "generate_project" or kind in {
        "greenfield",
        "benchmark_solution",
    } or config.get("generation_enabled") is True:
        return "greenfield"
    return "template"


def effective_experiment_template(config: Mapping[str, Any], default: str = GREENFIELD_TEMPLATE) -> str:
    """Resolve the design-stage template, including unified greenfield settings."""

    raw = str(config.get("experiment_template") or "").strip()
    mode = str(config.get("implementation_mode") or "").strip().lower()
    kind = str(config.get("task_kind") or "").strip().lower()
    if raw:
        return raw
    if mode == "generate_project" or kind in {"greenfield", "benchmark_solution"}:
        return GREENFIELD_TEMPLATE
    return default

