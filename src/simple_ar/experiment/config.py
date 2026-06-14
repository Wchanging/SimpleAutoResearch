"""Unified experiment/code task runtime configuration.

This module is intentionally small and JSON-friendly.  The public TOML loader
still accepts older sections, but downstream stages should read this normalized
shape when they need task, execution, resource, or evaluation settings.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping


@dataclass(slots=True)
class TaskSettings:
    kind: str = "auto"
    name: str = ""
    objective: str = ""
    task_file: str = ""
    code_root: str = ""
    output_root: str = ""


@dataclass(slots=True)
class ImplementationSettings:
    mode: str = "auto"
    domain_profile: str = "auto"
    provider: str = "local"
    task_handoff: str = "user_file"
    allow_external_agent: bool = False
    max_repair_attempts: int = 1


@dataclass(slots=True)
class WorkspaceSettings:
    mode: str = "copy"
    reuse_source_venv: bool = False
    setup_hook: str = ""
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ExecutionSettings:
    backend: str = "local"
    command: str = ""
    timeout_sec: int = 300
    stream_output: str = "auto"
    allow_dependency_install: bool = False


@dataclass(slots=True)
class ResourceSettings:
    max_runtime_sec: int = 300
    max_files: int = 12
    max_generated_lines: int = 1200
    max_memory_mb: int = 4096
    allow_gpu: bool = False


@dataclass(slots=True)
class EvaluationSettings:
    primary_metric: str = ""
    direction: str = "maximize"
    required_metrics: list[str] = field(default_factory=list)
    metric_directions: dict[str, str] = field(default_factory=dict)
    success_criteria: list[str] = field(default_factory=list)


@dataclass(slots=True)
class GenerationSettings:
    enabled: bool = False
    max_batches: int = 3
    files_per_batch: int = 4
    review_required: bool = True
    allow_fallback_scaffold: bool = False


@dataclass(slots=True)
class ModelRoleSettings:
    planner: str = ""
    implementer: str = ""
    reviewer: str = ""
    repairer: str = ""


@dataclass(slots=True)
class UnifiedTaskConfig:
    schema_version: str = "2.5"
    task: TaskSettings = field(default_factory=TaskSettings)
    implementation: ImplementationSettings = field(default_factory=ImplementationSettings)
    workspace: WorkspaceSettings = field(default_factory=WorkspaceSettings)
    execution: ExecutionSettings = field(default_factory=ExecutionSettings)
    resource: ResourceSettings = field(default_factory=ResourceSettings)
    evaluation: EvaluationSettings = field(default_factory=EvaluationSettings)
    generation: GenerationSettings = field(default_factory=GenerationSettings)
    models: ModelRoleSettings = field(default_factory=ModelRoleSettings)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def unified_task_config_from_runtime(config: Mapping[str, Any]) -> UnifiedTaskConfig:
    """Build a normalized task config from flattened pipeline config.

    The function also understands legacy code-task keys so older examples remain
    valid while new stages can consume one predictable shape.
    """

    existing = config.get("task_config")
    if isinstance(existing, Mapping):
        return _from_nested(existing)

    task_kind = _str(config.get("task_kind"), "auto")
    implementation_mode = _str(config.get("implementation_mode"), "auto")
    template = _str(config.get("experiment_template"), "")
    legacy_code_root = _str(config.get("code_task_code_root"), "")
    if task_kind == "auto" and (legacy_code_root or template == "code_task_project"):
        task_kind = "existing_project"
    if implementation_mode == "auto" and task_kind == "existing_project":
        implementation_mode = "patch_existing"

    primary_metric = _str(config.get("evaluation_primary_metric"), "")
    if not primary_metric:
        primary_metric = _str(config.get("code_task_primary_metric"), "")
    metric_directions = _str_dict(
        config.get("evaluation_metric_directions")
        or config.get("code_task_metric_directions")
        or {}
    )
    direction = _str(config.get("evaluation_direction"), "")
    if not direction and primary_metric in metric_directions:
        direction = _direction(metric_directions[primary_metric])
    if not direction:
        direction = "maximize"

    timeout_sec = _int(
        config.get("execution_timeout_sec"),
        _int(config.get("experiment_timeout_sec"), 300),
    )

    required_metrics = _str_list(config.get("evaluation_required_metrics"))
    if primary_metric and primary_metric not in required_metrics:
        required_metrics.insert(0, primary_metric)

    task = TaskSettings(
        kind=task_kind,
        name=_str(config.get("task_name"), _str(config.get("code_task_name"), "")),
        objective=_str(config.get("task_objective"), ""),
        task_file=_str(config.get("task_task_file"), _str(config.get("code_task_task_file"), "")),
        code_root=_str(config.get("task_code_root"), legacy_code_root),
        output_root=_str(config.get("task_output_root"), _str(config.get("output_root"), "")),
    )
    implementation = ImplementationSettings(
        mode=implementation_mode,
        domain_profile=_str(config.get("implementation_domain_profile"), "auto"),
        provider=_str(config.get("implementation_provider"), "local"),
        task_handoff=_handoff_mode(_str(config.get("implementation_task_handoff"), "user_file")),
        allow_external_agent=_bool(config.get("implementation_allow_external_agent"), False),
        max_repair_attempts=_int(config.get("implementation_max_repair_attempts"), 1),
    )
    workspace = WorkspaceSettings(
        mode=_str(config.get("workspace_mode"), _str(config.get("code_task_workspace_mode"), "copy")),
        reuse_source_venv=_bool(config.get("workspace_reuse_source_venv"), False),
        setup_hook=_str(config.get("workspace_setup_hook"), _str(config.get("environment_setup_hook"), "")),
        include=_str_list(config.get("workspace_include")),
        exclude=_str_list(config.get("workspace_exclude")),
    )
    execution = ExecutionSettings(
        backend=_str(config.get("execution_backend"), "local"),
        command=_str(config.get("execution_command"), _str(config.get("code_task_benchmark_command"), "")),
        timeout_sec=timeout_sec,
        stream_output=_str(
            config.get("execution_stream_output"),
            _str(config.get("code_task_stream_benchmark_output"), "auto"),
        ),
        allow_dependency_install=_bool(config.get("execution_allow_dependency_install"), False),
    )
    resource = ResourceSettings(
        max_runtime_sec=_int(config.get("resource_max_runtime_sec"), timeout_sec),
        max_files=_int(config.get("resource_max_files"), 12),
        max_generated_lines=_int(config.get("resource_max_generated_lines"), 1200),
        max_memory_mb=_int(config.get("resource_max_memory_mb"), 4096),
        allow_gpu=_bool(config.get("resource_allow_gpu"), False),
    )
    evaluation = EvaluationSettings(
        primary_metric=primary_metric,
        direction=_direction(direction),
        required_metrics=required_metrics,
        metric_directions={name: _direction(value) for name, value in metric_directions.items()},
        success_criteria=_str_list(config.get("evaluation_success_criteria")),
    )
    generation = GenerationSettings(
        enabled=_bool(config.get("generation_enabled"), False),
        max_batches=_int(config.get("generation_max_batches"), 3),
        files_per_batch=_int(config.get("generation_files_per_batch"), 4),
        review_required=_bool(config.get("generation_review_required"), True),
        allow_fallback_scaffold=_bool(config.get("generation_allow_fallback_scaffold"), False),
    )
    models = ModelRoleSettings(
        planner=_str(config.get("models_planner"), ""),
        implementer=_str(config.get("models_implementer"), ""),
        reviewer=_str(config.get("models_reviewer"), ""),
        repairer=_str(config.get("models_repairer"), ""),
    )
    return UnifiedTaskConfig(
        task=task,
        implementation=implementation,
        workspace=workspace,
        execution=execution,
        resource=resource,
        evaluation=evaluation,
        generation=generation,
        models=models,
    )


def _from_nested(data: Mapping[str, Any]) -> UnifiedTaskConfig:
    task = _mapping(data.get("task"))
    implementation = _mapping(data.get("implementation"))
    workspace = _mapping(data.get("workspace"))
    execution = _mapping(data.get("execution"))
    resource = _mapping(data.get("resource"))
    evaluation = _mapping(data.get("evaluation"))
    generation = _mapping(data.get("generation"))
    models = _mapping(data.get("models"))
    return UnifiedTaskConfig(
        schema_version=_str(data.get("schema_version"), "2.5"),
        task=TaskSettings(
            kind=_str(task.get("kind"), "auto"),
            name=_str(task.get("name"), ""),
            objective=_str(task.get("objective"), ""),
            task_file=_str(task.get("task_file"), ""),
            code_root=_str(task.get("code_root"), ""),
            output_root=_str(task.get("output_root"), ""),
        ),
        implementation=ImplementationSettings(
            mode=_str(implementation.get("mode"), "auto"),
            domain_profile=_str(implementation.get("domain_profile"), "auto"),
            provider=_str(implementation.get("provider"), "local"),
            task_handoff=_handoff_mode(_str(implementation.get("task_handoff"), "user_file")),
            allow_external_agent=_bool(implementation.get("allow_external_agent"), False),
            max_repair_attempts=_int(implementation.get("max_repair_attempts"), 1),
        ),
        workspace=WorkspaceSettings(
            mode=_str(workspace.get("mode"), "copy"),
            reuse_source_venv=_bool(workspace.get("reuse_source_venv"), False),
            setup_hook=_str(workspace.get("setup_hook"), ""),
            include=_str_list(workspace.get("include")),
            exclude=_str_list(workspace.get("exclude")),
        ),
        execution=ExecutionSettings(
            backend=_str(execution.get("backend"), "local"),
            command=_str(execution.get("command"), ""),
            timeout_sec=_int(execution.get("timeout_sec"), 300),
            stream_output=_str(execution.get("stream_output"), "auto"),
            allow_dependency_install=_bool(execution.get("allow_dependency_install"), False),
        ),
        resource=ResourceSettings(
            max_runtime_sec=_int(resource.get("max_runtime_sec"), 300),
            max_files=_int(resource.get("max_files"), 12),
            max_generated_lines=_int(resource.get("max_generated_lines"), 1200),
            max_memory_mb=_int(resource.get("max_memory_mb"), 4096),
            allow_gpu=_bool(resource.get("allow_gpu"), False),
        ),
        evaluation=EvaluationSettings(
            primary_metric=_str(evaluation.get("primary_metric"), ""),
            direction=_direction(_str(evaluation.get("direction"), "maximize")),
            required_metrics=_str_list(evaluation.get("required_metrics")),
            metric_directions={
                name: _direction(value)
                for name, value in _str_dict(evaluation.get("metric_directions")).items()
            },
            success_criteria=_str_list(evaluation.get("success_criteria")),
        ),
        generation=GenerationSettings(
            enabled=_bool(generation.get("enabled"), False),
            max_batches=_int(generation.get("max_batches"), 3),
            files_per_batch=_int(generation.get("files_per_batch"), 4),
            review_required=_bool(generation.get("review_required"), True),
            allow_fallback_scaffold=_bool(generation.get("allow_fallback_scaffold"), False),
        ),
        models=ModelRoleSettings(
            planner=_str(models.get("planner"), ""),
            implementer=_str(models.get("implementer"), ""),
            reviewer=_str(models.get("reviewer"), ""),
            repairer=_str(models.get("repairer"), ""),
        ),
    )


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _int(value: Any, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list | tuple):
        return [str(item) for item in value]
    return []


def _str_dict(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): str(item) for key, item in value.items()}


def _direction(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"higher", "high", "maximize", "max"}:
        return "maximize"
    if normalized in {"lower", "low", "minimize", "min"}:
        return "minimize"
    if normalized in {"resource", "cost"}:
        return "resource"
    if normalized in {"ignore", "none"}:
        return "ignore"
    return normalized or "maximize"


def _handoff_mode(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_")
    if normalized in {"merge", "merged", "merge_with_research", "user_and_research"}:
        return "merge"
    if normalized in {"generated", "generate", "research"}:
        return "generated"
    return "user_file"
