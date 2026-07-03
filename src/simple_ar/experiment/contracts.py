"""Experiment contract artifacts used by design and downstream code stages."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

from simple_ar.core.artifacts import write_json, write_text
from simple_ar.experiment.config import UnifiedTaskConfig, unified_task_config_from_runtime
from simple_ar.experiment.profiles import DomainProfile, resolve_domain_profile


@dataclass(slots=True)
class ResultSchema:
    primary_metric: str
    direction: str
    required_metrics: list[str] = field(default_factory=list)
    metric_directions: dict[str, str] = field(default_factory=dict)
    success_criteria: list[str] = field(default_factory=list)
    schema_version: str = "2.5"

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ResourcePlan:
    max_runtime_sec: int
    max_files: int
    max_generated_lines: int
    max_memory_mb: int
    allow_gpu: bool
    execution_backend: str
    stream_output: str
    rationale: list[str] = field(default_factory=list)
    schema_version: str = "2.5"

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class DependencyPlan:
    install_allowed: bool
    setup_hook: str = ""
    expected_entrypoints: list[str] = field(default_factory=list)
    candidate_entrypoints: list[str] = field(default_factory=list)
    required_packages: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    schema_version: str = "2.5"

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ExperimentContract:
    contract_id: str
    task_kind: str
    objective: str
    hypothesis: str
    implementation_mode: str
    template: str
    task_file: str
    code_root: str
    benchmark_command: str
    constraints: list[str]
    risks: list[str]
    result_schema: dict[str, Any]
    resource_plan: dict[str, Any]
    generation_plan: dict[str, Any]
    dependency_plan: dict[str, Any]
    domain_profile: dict[str, Any]
    schema_version: str = "2.5"

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ContractValidation:
    status: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    schema_version: str = "2.5"

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ExperimentDesignPackage:
    contract: ExperimentContract
    result_schema: ResultSchema
    resource_plan: ResourcePlan
    dependency_plan: DependencyPlan
    domain_profile: DomainProfile
    validation: ContractValidation


def build_experiment_design_package(
    config: Mapping[str, Any],
    *,
    topic: str,
    hypothesis: str,
    template: str,
    code_task: Mapping[str, Any] | None = None,
) -> ExperimentDesignPackage:
    runtime = unified_task_config_from_runtime(config)
    code_task = code_task or {}
    task_kind = _resolve_task_kind(runtime, template, code_task)
    implementation_mode = _resolve_implementation_mode(runtime, task_kind)
    domain_profile = resolve_domain_profile(runtime.implementation.domain_profile, task_kind=task_kind)
    result_schema = _build_result_schema(runtime, code_task, domain_profile, template, task_kind)
    resource_plan = _build_resource_plan(runtime)
    generation_plan = _build_generation_plan(runtime)
    dependency_plan = _build_dependency_plan(runtime, domain_profile)
    objective = _resolve_objective(runtime, topic, hypothesis)
    task_file = runtime.task.task_file or _str(code_task.get("task_file"))
    code_root = runtime.task.code_root or _str(code_task.get("code_root"))
    benchmark_command = runtime.execution.command or _str(code_task.get("benchmark_command"))
    constraints = _constraints(runtime, domain_profile)
    risks = _risks(runtime, task_kind, code_root, benchmark_command)
    contract = ExperimentContract(
        contract_id=_contract_id(objective, hypothesis, template, task_kind),
        task_kind=task_kind,
        objective=objective,
        hypothesis=_short_text(hypothesis, 1400),
        implementation_mode=implementation_mode,
        template=template,
        task_file=task_file,
        code_root=code_root,
        benchmark_command=benchmark_command,
        constraints=constraints,
        risks=risks,
        result_schema=result_schema.to_json(),
        resource_plan=resource_plan.to_json(),
        generation_plan=generation_plan,
        dependency_plan=dependency_plan.to_json(),
        domain_profile=domain_profile.to_json(),
    )
    validation = _validate_contract(contract, runtime)
    return ExperimentDesignPackage(
        contract=contract,
        result_schema=result_schema,
        resource_plan=resource_plan,
        dependency_plan=dependency_plan,
        domain_profile=domain_profile,
        validation=validation,
    )


def write_experiment_design_package(stage_dir: Path, package: ExperimentDesignPackage) -> None:
    write_json(stage_dir / "experiment_contract.json", package.contract.to_json())
    write_text(stage_dir / "experiment_contract.md", render_experiment_contract(package.contract))
    write_json(stage_dir / "result_schema.json", package.result_schema.to_json())
    write_json(stage_dir / "resource_plan.json", package.resource_plan.to_json())
    write_json(stage_dir / "dependency_plan.json", package.dependency_plan.to_json())
    write_json(stage_dir / "domain_profile.json", package.domain_profile.to_json())
    write_json(stage_dir / "contract_validation.json", package.validation.to_json())


def render_experiment_contract(contract: ExperimentContract) -> str:
    lines = [
        "# Experiment Contract",
        "",
        f"- Contract: `{contract.contract_id}`",
        f"- Task kind: `{contract.task_kind}`",
        f"- Implementation mode: `{contract.implementation_mode}`",
        f"- Template: `{contract.template}`",
    ]
    if contract.code_root:
        lines.append(f"- Code root: `{contract.code_root}`")
    if contract.task_file:
        lines.append(f"- Task file: `{contract.task_file}`")
    if contract.benchmark_command:
        lines.append(f"- Benchmark command: `{contract.benchmark_command}`")
    lines.extend(["", "## Objective", "", contract.objective or "(not specified)"])
    if contract.hypothesis:
        lines.extend(["", "## Hypothesis", "", contract.hypothesis])
    lines.extend(["", "## Result Schema", ""])
    result = contract.result_schema
    lines.append(f"- Primary metric: `{result.get('primary_metric', '')}`")
    lines.append(f"- Direction: `{result.get('direction', '')}`")
    required = result.get("required_metrics") or []
    if required:
        lines.append("- Required metrics: " + ", ".join(f"`{item}`" for item in required))
    if contract.constraints:
        lines.extend(["", "## Constraints", ""])
        lines.extend(f"- {item}" for item in contract.constraints)
    generation = contract.generation_plan
    if generation:
        lines.extend(["", "## Generation Budget", ""])
        lines.append(f"- Enabled: `{generation.get('enabled', False)}`")
        lines.append(f"- Max batches: `{generation.get('max_batches', '')}`")
        lines.append(f"- Files per batch: `{generation.get('files_per_batch', '')}`")
        lines.append(f"- Review required: `{generation.get('review_required', True)}`")
    if contract.risks:
        lines.extend(["", "## Risks", ""])
        lines.extend(f"- {item}" for item in contract.risks)
    return "\n".join(lines).rstrip() + "\n"


def _resolve_task_kind(
    runtime: UnifiedTaskConfig,
    template: str,
    code_task: Mapping[str, Any],
) -> str:
    if runtime.task.kind != "auto":
        return runtime.task.kind
    if code_task or template == "code_task_project":
        return "existing_project"
    if template == "greenfield_project":
        return "greenfield"
    if template:
        return "fixed_template"
    return "auto"


def _resolve_implementation_mode(runtime: UnifiedTaskConfig, task_kind: str) -> str:
    if runtime.implementation.mode != "auto":
        return runtime.implementation.mode
    if task_kind == "existing_project":
        return "patch_existing"
    if task_kind in {"greenfield", "benchmark_solution"}:
        return "generate_project"
    return "template"


def _build_result_schema(
    runtime: UnifiedTaskConfig,
    code_task: Mapping[str, Any],
    domain_profile: DomainProfile,
    template: str,
    task_kind: str,
) -> ResultSchema:
    primary = runtime.evaluation.primary_metric or _str(code_task.get("primary_metric"))
    if not primary and code_task:
        primary = "benchmark_passed"
    if not primary and task_kind in {"greenfield", "benchmark_solution"}:
        primary = "score"
    if not primary and template == "toy_text_classification":
        primary = "accuracy_delta"
    metric_directions = dict(runtime.evaluation.metric_directions)
    if primary and primary not in metric_directions:
        metric_directions[primary] = runtime.evaluation.direction
    required = list(runtime.evaluation.required_metrics)
    if not required and code_task:
        required.append("benchmark_passed")
    if not required and task_kind in {"greenfield", "benchmark_solution"}:
        required.append(primary or "score")
    if not required and template == "toy_text_classification":
        required.extend(["keyword_accuracy", "bow_logreg_accuracy", "accuracy_delta"])
    if primary and primary not in required:
        required.insert(0, primary)
    return ResultSchema(
        primary_metric=primary,
        direction=metric_directions.get(primary, runtime.evaluation.direction),
        required_metrics=required,
        metric_directions=metric_directions,
        success_criteria=list(runtime.evaluation.success_criteria),
    )


def _build_resource_plan(runtime: UnifiedTaskConfig) -> ResourcePlan:
    rationale = [
        "Use explicit runtime and file-count limits before implementation begins.",
        "Prefer smaller, reviewable batches unless generation settings request otherwise.",
    ]
    if not runtime.resource.allow_gpu:
        rationale.append("GPU use is disabled unless the task config opts in.")
    return ResourcePlan(
        max_runtime_sec=runtime.resource.max_runtime_sec,
        max_files=runtime.resource.max_files,
        max_generated_lines=runtime.resource.max_generated_lines,
        max_memory_mb=runtime.resource.max_memory_mb,
        allow_gpu=runtime.resource.allow_gpu,
        execution_backend=runtime.execution.backend,
        stream_output=runtime.execution.stream_output,
        rationale=rationale,
    )


def _build_generation_plan(runtime: UnifiedTaskConfig) -> dict[str, Any]:
    return {
        "enabled": runtime.generation.enabled,
        "max_batches": runtime.generation.max_batches,
        "files_per_batch": runtime.generation.files_per_batch,
        "review_required": runtime.generation.review_required,
        "planning_review_rounds": runtime.generation.planning_review_rounds,
    }


def _build_dependency_plan(runtime: UnifiedTaskConfig, domain_profile: DomainProfile) -> DependencyPlan:
    notes = [
        "Dependency installation must remain explicit and reproducible.",
        "Prefer project-local environments or sandbox setup hooks.",
    ]
    if not runtime.execution.allow_dependency_install:
        notes.append("Automatic dependency installation is disabled for this run.")
    return DependencyPlan(
        install_allowed=runtime.execution.allow_dependency_install,
        setup_hook=runtime.workspace.setup_hook,
        expected_entrypoints=_resolved_expected_entrypoints(runtime, domain_profile),
        candidate_entrypoints=list(domain_profile.expected_entrypoints),
        required_packages=[],
        notes=notes,
    )


def _resolved_expected_entrypoints(runtime: UnifiedTaskConfig, domain_profile: DomainProfile) -> list[str]:
    """Return entrypoints that are actual run obligations, not profile candidates.

    Domain profiles describe common entrypoint shapes for a task family.  Treating
    every candidate as required made downstream greenfield planning overfit to
    names such as ``train.py`` and ``benchmark.py`` even when the configured run
    command only expected one entrypoint.  The dependency plan keeps candidates
    separately and exposes only the concrete execution command here.
    """

    command = _short_text(runtime.execution.command, 240).strip()
    if command:
        return [command]
    if runtime.task.kind in {"greenfield", "benchmark_solution"}:
        return ["python main.py"]
    if domain_profile.expected_entrypoints:
        return [domain_profile.expected_entrypoints[0]]
    return []


def _resolve_objective(runtime: UnifiedTaskConfig, topic: str, hypothesis: str) -> str:
    base = runtime.task.objective or topic or _short_text(hypothesis, 300)
    task_spec = _task_file_excerpt(runtime.task.task_file)
    if not task_spec:
        return base
    if base:
        return _short_text(f"{base}\n\nTask file requirements:\n{task_spec}", 3500)
    return task_spec


def _constraints(runtime: UnifiedTaskConfig, domain_profile: DomainProfile) -> list[str]:
    constraints = [
        f"Workspace mode: {runtime.workspace.mode}.",
        f"Maximum generated/modified files: {runtime.resource.max_files}.",
        f"Maximum generated lines: {runtime.resource.max_generated_lines}.",
        f"Maximum runtime: {runtime.resource.max_runtime_sec}s.",
    ]
    constraints.extend(domain_profile.result_requirements[:3])
    task_spec = _task_file_excerpt(runtime.task.task_file, limit=900)
    if task_spec:
        constraints.append("Follow the task file requirements; excerpt: " + task_spec.replace("\n", " ")[:900])
    return constraints


def _risks(
    runtime: UnifiedTaskConfig,
    task_kind: str,
    code_root: str,
    benchmark_command: str,
) -> list[str]:
    risks: list[str] = []
    if task_kind == "existing_project" and not code_root:
        risks.append("Existing-project tasks need a code_root before code changes can run safely.")
    if task_kind in {"existing_project", "benchmark_solution"} and not benchmark_command:
        risks.append("No benchmark command is configured; validation may be limited.")
    if runtime.generation.enabled and runtime.resource.max_files > 20:
        risks.append("Large generation budgets should be split into resumable batches.")
    if runtime.execution.allow_dependency_install:
        risks.append("Dependency installation can change environment state; keep setup logs for audit.")
    return risks


def _validate_contract(contract: ExperimentContract, runtime: UnifiedTaskConfig) -> ContractValidation:
    errors: list[str] = []
    warnings: list[str] = []
    if not contract.objective:
        errors.append("Missing task objective.")
    if contract.task_kind == "existing_project" and not contract.code_root:
        errors.append("existing_project tasks require code_root.")
    if not contract.result_schema.get("primary_metric"):
        warnings.append("No primary metric configured; downstream comparison may be weak.")
    if not contract.benchmark_command and contract.task_kind in {"existing_project", "benchmark_solution"}:
        warnings.append("No benchmark command configured.")
    if runtime.resource.max_runtime_sec <= 0:
        errors.append("resource.max_runtime_sec must be positive.")
    status = "failed" if errors else ("warning" if warnings else "passed")
    return ContractValidation(status=status, errors=errors, warnings=warnings)


def _contract_id(objective: str, hypothesis: str, template: str, task_kind: str) -> str:
    digest = hashlib.sha1(
        "\n".join([objective, hypothesis[:500], template, task_kind]).encode("utf-8")
    ).hexdigest()[:10]
    return f"exp-{digest}"


def _short_text(value: str, limit: int) -> str:
    value = value.strip()
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."


def _task_file_excerpt(path_value: str, *, limit: int = 2400) -> str:
    path_value = path_value.strip()
    if not path_value:
        return ""
    path = Path(path_value)
    if not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    return _short_text(text, limit)


def _str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)
