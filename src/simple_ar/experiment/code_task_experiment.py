"""Compatibility facade for embedded code-task experiment helpers.

New code should import from ``simple_ar.experiment.code_task_bridge``.  This
module remains so older imports keep working while the bridge logic is split
across spec, runner, and artifact modules.
"""

from simple_ar.experiment.code_task_bridge import (
    CODE_TASK_PROJECT_TEMPLATE,
    CODE_TASK_TOY_SPAM_BENCHMARK,
    CODE_TASK_TOY_SPAM_TEMPLATE,
    CodeTaskExperimentResult,
    CodeTaskExperimentSpec,
    build_code_task_experiment_script,
    code_task_experiment_spec,
    code_task_project_spec,
    code_task_toy_spam_spec,
    is_code_task_experiment_template,
    prepare_code_task_experiment,
    write_code_task_experiment_meta,
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
    "write_code_task_experiment_meta",
]
