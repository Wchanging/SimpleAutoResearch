from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from simple_ar.code_task.comparison import normalize_metric_direction


DEFAULT_OUTPUT_ROOT = "runs"
DEFAULT_MAX_FILE_BYTES = 2_000_000
DEFAULT_ENV_MODE = "current"
DEFAULT_WORKSPACE_MODE = "copy"
VALID_EXECUTE_STEPS = {
    "probe",
    "baseline",
    "work-plan",
    "batch",
    "plan",
    "propose-edits",
    "apply-edits",
    "validate",
    "run",
    "analyze-failure",
    "repair",
}


class CodeTaskConfigError(RuntimeError):
    """Raised when a code-task config file or override is invalid."""


@dataclass(frozen=True)
class CodeTaskInitOptions:
    """Resolved options for ``code-task init`` after config/CLI merging."""

    code_root: str
    task_file: str | None
    output_root: str
    name: str | None
    benchmark_command: str | None
    max_file_bytes: int
    workspace_mode: str
    workspace_include: tuple[str, ...]
    workspace_exclude: tuple[str, ...]
    workspace_reuse_source_venv: bool
    workspace_setup_hook: str
    env_mode: str
    python_executable: str | None
    primary_metric: str | None
    metric_directions: dict[str, str]
    config_path: str | None


@dataclass(frozen=True)
class CodeTaskExecuteOptions:
    """Resolved options for ``code-task execute`` from a TOML config.

    The config is intentionally optional. It keeps the common long-tail knobs
    out of the CLI happy path while preserving explicit CLI overrides in
    ``cli.py``.
    """

    to_step: str
    model: str | None
    planner_model: str | None
    editor_model: str | None
    repair_model: str | None
    summarizer_model: str | None
    use_llm: bool
    timeout_sec: int
    skip_validation: bool
    strict_validation: bool
    validation_max_file_bytes: int
    apply_proposed_edits: bool
    allow_large_edits: bool
    repair_rounds: int
    budget_profile: str | None
    edit_budget_overrides: dict[str, Any]
    max_batches: int | None
    cost_cap_usd: float | None
    max_files: int
    max_source_chars_per_file: int
    env_mode: str | None
    python_executable: str | None
    config_path: str | None


def parse_metric_direction_arg(value: str) -> tuple[str, str]:
    """Parse and normalize ``METRIC=DIRECTION`` strings."""
    separator = "=" if "=" in value else ":"
    if separator not in value:
        raise CodeTaskConfigError(
            "Expected METRIC=DIRECTION, for example accuracy=higher"
        )
    name, direction = value.split(separator, 1)
    name = name.strip()
    direction = direction.strip()
    if not name or not direction:
        raise CodeTaskConfigError("Expected non-empty METRIC and DIRECTION values")
    try:
        normalized = normalize_metric_direction(direction)
    except ValueError as exc:
        raise CodeTaskConfigError(str(exc)) from exc
    return name, normalized


def load_code_task_execute_options(
    *,
    config_path: str | None = None,
) -> CodeTaskExecuteOptions:
    """Resolve optional ``code-task execute`` settings from TOML.

    Supported sections:
    - ``[execute]`` for orchestration knobs.
    - ``[models.code_task]`` for planner/editor/repair/summarizer models.
    - ``[budget]`` plus optional ``[budget.normal]``/``[budget.large]`` for
      edit budget profile and caps.
    - ``[environment]`` for env mode and Python executable.
    """

    config = _load_toml_config(config_path)
    execute = _config_table(config, "execute")
    benchmark = _config_table(config, "benchmark")
    llm = _config_table(config, "llm")
    models = _config_table(config, "models")
    code_task_models = _config_table(models, "code_task")
    budget = _config_table(config, "budget")
    environment = _config_table(config, "environment")
    safety = _config_table(config, "safety")

    to_step = _config_string(execute.get("to_step")) or "run"
    if to_step not in VALID_EXECUTE_STEPS:
        raise CodeTaskConfigError(
            "Unsupported [execute].to_step. Expected one of: "
            + ", ".join(sorted(VALID_EXECUTE_STEPS))
        )
    budget_profile = (
        _config_string(execute.get("budget_profile"))
        or _config_string(budget.get("profile"))
    )
    max_batches = _config_int(execute.get("max_batches"))
    if max_batches is None:
        max_batches = _config_int(budget.get("max_batches"))
    cost_cap = _config_float(execute.get("cost_cap_usd"))
    if cost_cap is None:
        cost_cap = _config_float(budget.get("cost_cap_usd"))

    return CodeTaskExecuteOptions(
        to_step=to_step,
        model=(
            _config_string(execute.get("model"))
            or _config_string(code_task_models.get("default"))
            or _config_string(models.get("default"))
            or _config_string(llm.get("model"))
        ),
        planner_model=_config_string(code_task_models.get("planner")),
        editor_model=_config_string(code_task_models.get("editor")),
        repair_model=_config_string(code_task_models.get("repair")),
        summarizer_model=_config_string(code_task_models.get("summarizer")),
        use_llm=_resolve_bool(
            override=None,
            value=execute.get("use_llm", llm.get("enabled")),
            default=True,
        ),
        timeout_sec=_positive_int(
            _config_int(execute.get("timeout_sec"))
            or _config_int(execute.get("timeout"))
            or _config_int(benchmark.get("timeout")),
            60,
        ),
        skip_validation=_resolve_bool(
            override=None,
            value=execute.get("skip_validation"),
            default=False,
        ),
        strict_validation=_resolve_bool(
            override=None,
            value=execute.get("strict_validation"),
            default=False,
        ),
        validation_max_file_bytes=_positive_int(
            _config_int(execute.get("validation_max_file_bytes"))
            or _config_int(safety.get("validation_max_file_bytes")),
            500_000,
        ),
        apply_proposed_edits=_resolve_bool(
            override=None,
            value=execute.get("apply_proposed_edits"),
            default=False,
        ),
        allow_large_edits=_resolve_bool(
            override=None,
            value=execute.get("allow_large_edits"),
            default=False,
        ),
        repair_rounds=_non_negative_int(_config_int(execute.get("repair_rounds")), 0),
        budget_profile=budget_profile,
        edit_budget_overrides=_edit_budget_overrides(budget, budget_profile),
        max_batches=max_batches if max_batches and max_batches > 0 else None,
        cost_cap_usd=cost_cap if cost_cap is not None and cost_cap >= 0 else None,
        max_files=_positive_int(_config_int(execute.get("max_files")), 8),
        max_source_chars_per_file=_positive_int(
            _config_int(execute.get("max_source_chars_per_file")),
            4000,
        ),
        env_mode=_config_string(environment.get("mode")),
        python_executable=(
            _config_string(environment.get("python"))
            or _config_string(environment.get("python_executable"))
        ),
        config_path=config_path,
    )


def load_code_task_init_options(
    *,
    config_path: str | None = None,
    code_root: str | None = None,
    task_file: str | None = None,
    output_root: str | None = None,
    name: str | None = None,
    benchmark_command: str | None = None,
    max_file_bytes: int | None = None,
    workspace_mode: str | None = None,
    workspace_include: list[str] | tuple[str, ...] | None = None,
    workspace_exclude: list[str] | tuple[str, ...] | None = None,
    workspace_reuse_source_venv: bool | None = None,
    workspace_setup_hook: str | None = None,
    env_mode: str | None = None,
    python_executable: str | None = None,
    primary_metric: str | None = None,
    metric_directions: list[tuple[str, str]] | dict[str, str] | None = None,
    require_task_file: bool = True,
) -> CodeTaskInitOptions:
    """Resolve ``code-task init`` options from TOML config plus CLI overrides.

    Explicit arguments take precedence over the config file. The resulting
    object is intentionally small and maps directly to ``initialize_code_task``.
    ``require_task_file`` stays true for standalone code-task runs; embedded
    pipeline runs may generate a task file from earlier research artifacts.
    """
    config = _load_toml_config(config_path)
    code_task = _config_table(config, "code_task")
    benchmark = _config_table(config, "benchmark")
    metrics = _config_table(config, "metrics")
    environment = _config_table(config, "environment")
    workspace = _config_table(config, "workspace")
    safety = _config_table(config, "safety")

    resolved_code_root = _config_string(code_root) or _config_string(
        code_task.get("code_root")
    )
    resolved_task_file = _config_string(task_file) or _config_string(
        code_task.get("task_file")
    )
    if not resolved_code_root:
        raise CodeTaskConfigError(
            "Missing code root. Pass --code-root or set [code_task].code_root."
        )
    if require_task_file and not resolved_task_file:
        raise CodeTaskConfigError(
            "Missing task file. Pass --task-file or set [code_task].task_file."
        )

    resolved_output_root = (
        _config_string(output_root)
        or _config_string(code_task.get("output_root"))
        or DEFAULT_OUTPUT_ROOT
    )
    resolved_name = _config_string(name) or _config_string(code_task.get("name"))
    resolved_benchmark_command = _config_string(benchmark_command) or _config_string(
        benchmark.get("command")
    )
    resolved_primary_metric = (
        _config_string(primary_metric)
        or _config_string(benchmark.get("primary_metric"))
        or _config_string(metrics.get("primary"))
        or _config_string(metrics.get("primary_metric"))
    )
    resolved_env_mode = (
        _config_string(env_mode)
        or _config_string(environment.get("mode"))
        or DEFAULT_ENV_MODE
    )
    resolved_python = (
        _config_string(python_executable)
        or _config_string(environment.get("python"))
        or _config_string(environment.get("python_executable"))
    )
    resolved_workspace_mode = (
        _config_string(workspace_mode)
        or _config_string(workspace.get("mode"))
        or DEFAULT_WORKSPACE_MODE
    )
    resolved_reuse_source_venv = _resolve_bool(
        override=workspace_reuse_source_venv,
        value=workspace.get("reuse_source_venv"),
        default=False,
    )
    resolved_setup_hook = _config_string(workspace_setup_hook) or _config_string(
        workspace.get("setup_hook")
    )
    resolved_workspace_include = _resolve_string_list(
        override=workspace_include,
        value=workspace.get("include"),
    )
    resolved_workspace_exclude = _resolve_string_list(
        override=workspace_exclude,
        value=workspace.get("exclude"),
    )

    return CodeTaskInitOptions(
        code_root=resolved_code_root,
        task_file=resolved_task_file,
        output_root=resolved_output_root,
        name=resolved_name,
        benchmark_command=resolved_benchmark_command,
        max_file_bytes=_resolve_max_file_bytes(
            override=max_file_bytes,
            code_task=code_task,
            safety=safety,
        ),
        workspace_mode=resolved_workspace_mode,
        workspace_include=resolved_workspace_include,
        workspace_exclude=resolved_workspace_exclude,
        workspace_reuse_source_venv=resolved_reuse_source_venv,
        workspace_setup_hook=resolved_setup_hook or "",
        env_mode=resolved_env_mode,
        python_executable=resolved_python,
        primary_metric=resolved_primary_metric,
        metric_directions=_merge_metric_directions(
            config=config,
            cli_values=metric_directions,
        ),
        config_path=config_path,
    )


def _load_toml_config(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    config_path = Path(path)
    try:
        with config_path.open("rb") as handle:
            data = tomllib.load(handle)
    except FileNotFoundError as exc:
        raise CodeTaskConfigError(f"Config file not found: {config_path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise CodeTaskConfigError(
            f"Could not parse TOML config {config_path}: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise CodeTaskConfigError(f"Expected TOML table in config file: {config_path}")
    return data


def _config_table(config: dict[str, Any], key: str) -> dict[str, Any]:
    value = config.get(key)
    return value if isinstance(value, dict) else {}


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


def _config_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _resolve_bool(*, override: bool | None, value: object, default: bool) -> bool:
    if override is not None:
        return override
    if isinstance(value, bool):
        return value
    return default


def _resolve_string_list(
    *,
    override: list[str] | tuple[str, ...] | None,
    value: object,
) -> tuple[str, ...]:
    if override is not None:
        return _config_string_list(override)
    return _config_string_list(value)


def _config_string_list(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    result: list[str] = []
    for item in value:
        text = _config_string(item)
        if text:
            result.append(text)
    return tuple(dict.fromkeys(result))


def _resolve_max_file_bytes(
    *,
    override: int | None,
    code_task: dict[str, Any],
    safety: dict[str, Any],
) -> int:
    if override is not None:
        return override
    configured = _config_int(code_task.get("max_file_bytes"))
    if configured is not None:
        return configured
    safety_value = _config_int(safety.get("max_file_bytes"))
    if safety_value is not None:
        return safety_value
    return DEFAULT_MAX_FILE_BYTES


def _positive_int(value: int | None, default: int) -> int:
    if isinstance(value, int) and value > 0:
        return value
    return default


def _non_negative_int(value: int | None, default: int) -> int:
    if isinstance(value, int) and value >= 0:
        return value
    return default


def _edit_budget_overrides(
    budget: dict[str, Any],
    profile: str | None,
) -> dict[str, Any]:
    """Return numeric edit budget overrides for the selected profile."""

    keys = {
        "max_files",
        "max_edits",
        "max_old_chars",
        "max_new_chars",
        "max_total_edit_chars",
        "max_proposal_chars",
    }
    values: dict[str, Any] = {}
    for key in keys:
        if key in budget:
            values[key] = budget[key]
    selected_profile = (profile or _config_string(budget.get("profile")) or "normal").strip().lower()
    profile_table = _config_table(budget, selected_profile)
    for key in keys:
        if key in profile_table:
            values[key] = profile_table[key]
    return values


def _merge_metric_directions(
    *,
    config: dict[str, Any],
    cli_values: list[tuple[str, str]] | dict[str, str] | None,
) -> dict[str, str]:
    benchmark = _config_table(config, "benchmark")
    metrics = _config_table(config, "metrics")
    directions: dict[str, str] = {}
    for table in (
        benchmark.get("metric_directions"),
        metrics.get("directions"),
        metrics.get("metric_directions"),
    ):
        if isinstance(table, dict):
            directions.update(_normalize_direction_table(table))
    directions.update(_normalize_cli_directions(cli_values))
    return directions


def _normalize_direction_table(table: dict[str, object]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in table.items():
        name = str(key).strip()
        if not name:
            continue
        try:
            result[name] = normalize_metric_direction(str(value))
        except ValueError as exc:
            raise CodeTaskConfigError(str(exc)) from exc
    return result


def _normalize_cli_directions(
    value: list[tuple[str, str]] | dict[str, str] | None,
) -> dict[str, str]:
    if not value:
        return {}
    if isinstance(value, dict):
        return _normalize_direction_table(value)
    return _normalize_direction_table(dict(value))
