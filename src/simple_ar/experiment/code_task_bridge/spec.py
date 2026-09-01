from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from simple_ar.code_task import load_code_task_init_options
from simple_ar.code_task.runtime.config import DEFAULT_MAX_FILE_BYTES, CodeTaskConfigError


CODE_TASK_TOY_SPAM_TEMPLATE = "llm_code_task_toy_spam"
CODE_TASK_PROJECT_TEMPLATE = "code_task_project"
CODE_TASK_TOY_SPAM_BENCHMARK = "python -m unittest discover -s tests"
MessageCallback = Callable[[str], None]


@dataclass(frozen=True)
class CodeTaskExperimentSpec:
    """Source and runtime configuration for an embedded code-task experiment."""

    template: str
    code_root: Path
    task_file: Path | None
    benchmark_command: str | None
    primary_metric: str | None = None
    metric_directions: dict[str, str] = field(default_factory=dict)
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES
    workspace_mode: str = "auto"
    workspace_include: tuple[str, ...] = ()
    workspace_exclude: tuple[str, ...] = ()
    workspace_reuse_source_venv: bool = False
    workspace_setup_hook: str = ""
    env_mode: str = "current"
    python_executable: str | None = None
    edit_scope_mode: str | None = None
    edit_scope_allowed_patterns: tuple[str, ...] = ()
    edit_scope_protected_patterns: tuple[str, ...] = ()
    config_path: str | None = None
    name: str | None = None
    allow_test_changes: bool = False
    allow_large_edits: bool = False
    approval_note: str = "Auto-approved inside isolated 8-stage code-task workspace."

    def result_schema(self) -> dict[str, object]:
        """Return the metric contract enforced by the Code-Task configuration."""

        primary = str(self.primary_metric or "").strip()
        directions = {
            str(name): str(direction)
            for name, direction in self.metric_directions.items()
            if str(name).strip()
        }
        required = list(dict.fromkeys(([primary] if primary else []) + list(directions)))
        schema: dict[str, object] = {"required_metrics": required}
        if primary:
            schema["primary_metric"] = primary
        if directions:
            schema["metric_directions"] = directions
        return schema


@dataclass(frozen=True)
class CodeTaskExperimentResult:
    """Compact result returned after preparing an embedded code-task run."""

    code_task_run_dir: Path
    workspace_dir: Path
    patch_plan_path: Path
    proposed_edits_path: Path
    patch_diff_path: Path
    validation_report_path: Path
    plan_mode: str
    edit_mode: str
    edit_count: int
    changed_files: tuple[str, ...]
    validation_status: str
    template: str = CODE_TASK_TOY_SPAM_TEMPLATE
    baseline_status: str = ""
    environment_report_path: Path | None = None
    baseline_report_path: Path | None = None
    repo_map_path: Path | None = None
    repo_map_summary_path: Path | None = None
    context_pack_path: Path | None = None
    context_prompt_path: Path | None = None
    context_snippets_path: Path | None = None
    work_plan_path: Path | None = None
    work_plan_markdown_path: Path | None = None
    work_plan_mode: str = ""
    work_plan_item_count: int = 0
    attempt_id: str = ""
    attempt_state_path: Path | None = None
    batch_id: str = ""
    batch_state_path: Path | None = None
    work_item_id: str = ""
    summary_path: Path | None = None


def is_code_task_experiment_template(template: object) -> bool:
    return str(template) in {CODE_TASK_TOY_SPAM_TEMPLATE, CODE_TASK_PROJECT_TEMPLATE}


def code_task_toy_spam_spec(repo_root: Path) -> CodeTaskExperimentSpec:
    root = Path(repo_root)
    return CodeTaskExperimentSpec(
        template=CODE_TASK_TOY_SPAM_TEMPLATE,
        code_root=root / "tests" / "fixtures" / "code_tasks" / "toy_spam_project",
        task_file=root / "tests" / "fixtures" / "code_tasks" / "improve_toy_spam_baseline.md",
        benchmark_command=CODE_TASK_TOY_SPAM_BENCHMARK,
        allow_test_changes=False,
        approval_note="Auto-approved inside isolated 8-stage demo workspace.",
    )


def code_task_project_spec(
    config: dict[str, object],
    *,
    task_file_override: Path | None = None,
) -> CodeTaskExperimentSpec:
    """Resolve a generic user-project code-task spec from pipeline config."""

    try:
        options = load_code_task_init_options(
            config_path=_config_string(config.get("code_task_config")),
            code_root=_config_string(config.get("code_task_code_root")),
            task_file=_config_string(config.get("code_task_task_file")),
            output_root=None,
            name=_config_string(config.get("code_task_name")),
            benchmark_command=_config_string(config.get("code_task_benchmark_command")),
            max_file_bytes=_config_int(config.get("code_task_max_file_bytes")),
            workspace_mode=_config_string(config.get("code_task_workspace_mode")),
            workspace_reuse_source_venv=_config_bool(
                config.get("code_task_workspace_reuse_source_venv")
            ),
            workspace_setup_hook=_config_string(config.get("code_task_workspace_setup_hook")),
            env_mode=_config_string(config.get("code_task_env_mode")),
            python_executable=_config_string(config.get("code_task_python_executable")),
            primary_metric=_config_string(config.get("code_task_primary_metric")),
            metric_directions=_config_metric_directions(config.get("code_task_metric_directions")),
            require_task_file=False,
        )
    except CodeTaskConfigError as exc:
        raise RuntimeError(f"Invalid code-task experiment configuration: {exc}") from exc

    task_file = task_file_override
    if task_file is None and options.task_file:
        task_file = _resolve_user_path(options.task_file)
    return CodeTaskExperimentSpec(
        template=CODE_TASK_PROJECT_TEMPLATE,
        code_root=_resolve_user_path(options.code_root),
        task_file=task_file,
        benchmark_command=options.benchmark_command,
        primary_metric=options.primary_metric,
        metric_directions=options.metric_directions,
        max_file_bytes=options.max_file_bytes,
        workspace_mode=options.workspace_mode,
        workspace_include=options.workspace_include,
        workspace_exclude=options.workspace_exclude,
        workspace_reuse_source_venv=options.workspace_reuse_source_venv,
        workspace_setup_hook=options.workspace_setup_hook,
        env_mode=options.env_mode,
        python_executable=options.python_executable,
        edit_scope_mode=options.edit_scope_mode,
        edit_scope_allowed_patterns=options.edit_scope_allowed_patterns,
        edit_scope_protected_patterns=options.edit_scope_protected_patterns,
        config_path=options.config_path,
        name=options.name,
        allow_test_changes=False,
        allow_large_edits=_config_bool(config.get("safety_allow_large_edits")) or False,
    )


def code_task_experiment_spec(
    repo_root: Path,
    config: dict[str, object],
    *,
    task_file_override: Path | None = None,
) -> CodeTaskExperimentSpec:
    template = str(config.get("experiment_template", "")).strip()
    if template == CODE_TASK_TOY_SPAM_TEMPLATE:
        return code_task_toy_spam_spec(repo_root)
    if template == CODE_TASK_PROJECT_TEMPLATE:
        return code_task_project_spec(config, task_file_override=task_file_override)
    raise RuntimeError(f"Unsupported code-task experiment template: {template}")


def _resolve_user_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (Path.cwd() / path)


def _config_string(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _config_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _config_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _config_metric_directions(value: object) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    result: dict[str, str] = {}
    for key, direction in value.items():
        name = str(key).strip()
        if name:
            result[name] = str(direction)
    return result

