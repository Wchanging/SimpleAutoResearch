"""Embedded code-task bridge for 8-stage experiment runs."""

from simple_ar.experiment.code_task_bridge.artifacts import (
    build_code_task_experiment_script,
    write_code_task_experiment_meta,
)
from simple_ar.experiment.code_task_bridge.design import resolve_code_task_design_task
from simple_ar.experiment.code_task_bridge.runner import prepare_code_task_experiment
from simple_ar.experiment.code_task_bridge.spec import (
    CODE_TASK_PROJECT_TEMPLATE,
    CODE_TASK_TOY_SPAM_BENCHMARK,
    CODE_TASK_TOY_SPAM_TEMPLATE,
    CodeTaskExperimentResult,
    CodeTaskExperimentSpec,
    code_task_experiment_spec,
    code_task_project_spec,
    code_task_toy_spam_spec,
    is_code_task_experiment_template,
)

__all__ = [
    "CODE_TASK_PROJECT_TEMPLATE",
    "CODE_TASK_TOY_SPAM_BENCHMARK",
    "CODE_TASK_TOY_SPAM_TEMPLATE",
    "CodeTaskExperimentResult",
    "CodeTaskExperimentSpec",
    "build_code_task_experiment_script",
    "code_task_experiment_spec",
    "code_task_project_spec",
    "code_task_toy_spam_spec",
    "is_code_task_experiment_template",
    "prepare_code_task_experiment",
    "resolve_code_task_design_task",
    "write_code_task_experiment_meta",
]
