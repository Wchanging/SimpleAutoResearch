from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from simple_ar.experiment.config import unified_task_config_from_runtime


class RunConfigError(RuntimeError):
    """Raised when a top-level run config file is missing or invalid."""


class _ConfigModel(BaseModel):
    """Base model for TOML sections.

    Config files are user-facing and may contain future keys while the project
    evolves. Unknown keys are ignored here and can be picked up by later
    versions without breaking older installations.
    """

    model_config = ConfigDict(extra="ignore")


class RunSection(_ConfigModel):
    topic: str | None = None
    output_root: str | None = None
    from_stage: str | None = None
    to_stage: str | None = None
    quiet: bool | None = None
    debug_artifacts: bool | None = None
    overwrite_stage_artifacts: bool | None = None


class LLMSection(_ConfigModel):
    enabled: bool | None = None
    model: str | None = None
    workers: int | None = None


class SearchSection(_ConfigModel):
    offline: bool | None = None
    max_papers: int | None = None
    query: str | None = None
    allow_fixture_fallback: bool | None = None
    strict: bool | None = None


class ResearchBudgetSection(_ConfigModel):
    max_documents: int | None = None
    max_chunks: int | None = None
    max_context_tokens: int | None = None
    max_llm_calls: int | None = None
    max_follow_up_queries: int | None = None
    novelty_backend: str | None = None


class ResearchSection(_ConfigModel):
    mode: str | None = None
    planner: str | None = None
    sources: list[str] | None = None
    queries: list[str] | None = None
    auto_query_expansion: bool | None = None
    max_retrieval_rounds: int | None = None
    max_queries: int | None = None
    required_facets: list[str] | None = None
    use_fulltext: bool | None = None
    allow_pdf_download: bool | None = None
    max_fulltext_documents: int | None = None
    max_pdf_mb: int | None = None
    keep_raw_pdf: bool | None = None
    parser_backend: str | None = None
    read_screening: str | None = None
    read_batch_size: int | None = None
    read_workers: int | None = None
    read_min_shortlist: int | None = None
    read_max_shortlist: int | None = None
    cache: bool | None = None
    index_backend: str | None = None
    index_root: str | None = None
    local_documents: list[str] | None = None
    budget: ResearchBudgetSection = Field(default_factory=ResearchBudgetSection)


class RetrievalSection(_ConfigModel):
    enabled: bool | None = None
    top_k: int | None = None


class ExperimentSection(_ConfigModel):
    template: str | None = None
    timeout: int | None = None
    code_task_config: str | None = None


class TaskSection(_ConfigModel):
    kind: str | None = None
    name: str | None = None
    objective: str | None = None
    task_file: str | None = None
    code_root: str | None = None
    output_root: str | None = None


class ImplementationSection(_ConfigModel):
    mode: str | None = None
    domain_profile: str | None = None
    provider: str | None = None
    agent_mode: str | None = None
    agent_model: str | None = None
    agent_binary: str | None = None
    agent_args: list[str] | None = None
    agent_timeout_sec: int | None = None
    task_handoff: str | None = None
    allow_external_agent: bool | None = None
    max_repair_attempts: int | None = None


class WorkspaceSection(_ConfigModel):
    mode: str | None = None
    reuse_source_venv: bool | None = None
    setup_hook: str | None = None
    include: list[str] | None = None
    exclude: list[str] | None = None


class ExecutionSection(_ConfigModel):
    backend: str | None = None
    command: str | None = None
    timeout_sec: int | None = None
    stream_output: str | None = None
    baseline_policy: str | None = None
    baseline_metrics_file: str | None = None
    allow_dependency_install: bool | None = None


class ResourceSection(_ConfigModel):
    max_runtime_sec: int | None = None
    max_files: int | None = None
    max_generated_lines: int | None = None
    max_memory_mb: int | None = None
    allow_gpu: bool | None = None


class EvaluationSection(_ConfigModel):
    primary_metric: str | None = None
    direction: str | None = None
    required_metrics: list[str] | None = None
    metric_directions: dict[str, str] = Field(default_factory=dict)
    success_criteria: list[str] | None = None


class GenerationSection(_ConfigModel):
    enabled: bool | None = None
    max_batches: int | None = None
    files_per_batch: int | None = None
    review_required: bool | None = None
    planning_review_rounds: int | None = None
    allow_fallback_scaffold: bool | None = None


class ModelsSection(_ConfigModel):
    planner: str | None = None
    implementer: str | None = None
    reviewer: str | None = None
    repairer: str | None = None


class EditScopeSection(_ConfigModel):
    allow: list[str] | None = None
    deny: list[str] | None = None


class SafetySection(_ConfigModel):
    allow_large_edits: bool | None = None
    require_review: bool | None = None


class ReportSection(_ConfigModel):
    mode: str | None = None
    template: str | None = None
    criteria: str | None = None
    style: str | None = None
    cost_profile: str | None = None
    outline_strategy: str | None = None
    survey_contract: bool | None = None
    draft_sections: bool | None = None
    debug_artifacts: bool | None = None
    agent: str | None = None
    reviewer: str | None = None
    max_review_iterations: int | None = None
    max_section_tokens: int | None = None
    max_report_tokens: int | None = None
    max_section_sources: int | None = None
    source_strategy: str | None = None
    source_batch_size: int | None = None
    max_source_batches: int | None = None
    review_source_batches: bool | None = None
    review_trace: str | None = None
    output_mode: str | None = None
    output_label: str | None = None
    allow_source_backtracking: bool | None = None
    max_backtracking_calls: int | None = None
    max_backtracking_tokens: int | None = None
    figures: dict[str, Any] = Field(default_factory=dict)
    audit: dict[str, Any] = Field(default_factory=dict)


class PipelineRunConfig(_ConfigModel):
    run: RunSection = Field(default_factory=RunSection)
    llm: LLMSection = Field(default_factory=LLMSection)
    search: SearchSection = Field(default_factory=SearchSection)
    research: ResearchSection = Field(default_factory=ResearchSection)
    retrieval: RetrievalSection = Field(default_factory=RetrievalSection)
    experiment: ExperimentSection = Field(default_factory=ExperimentSection)
    task: TaskSection = Field(default_factory=TaskSection)
    implementation: ImplementationSection = Field(default_factory=ImplementationSection)
    workspace: WorkspaceSection = Field(default_factory=WorkspaceSection)
    execution: ExecutionSection = Field(default_factory=ExecutionSection)
    resource: ResourceSection = Field(default_factory=ResourceSection)
    evaluation: EvaluationSection = Field(default_factory=EvaluationSection)
    generation: GenerationSection = Field(default_factory=GenerationSection)
    models: ModelsSection = Field(default_factory=ModelsSection)
    edit_scope: EditScopeSection = Field(default_factory=EditScopeSection)
    safety: SafetySection = Field(default_factory=SafetySection)
    report: ReportSection = Field(default_factory=ReportSection)

    def flatten(self, *, config_path: Path, raw_data: dict[str, Any]) -> dict[str, object]:
        """Convert typed TOML sections to the existing runtime config dict."""
        result: dict[str, object] = {}

        _set_string(result, "topic", self.run.topic)
        _set_path_string(result, "output_root", self.run.output_root)
        _set_string(result, "from_stage", self.run.from_stage)
        _set_string(result, "to_stage", self.run.to_stage)
        _set_bool(result, "quiet", self.run.quiet)
        _set_bool(result, "debug_artifacts", self.run.debug_artifacts)
        _set_bool(result, "overwrite_stage_artifacts", self.run.overwrite_stage_artifacts)

        if isinstance(self.llm.enabled, bool):
            result["use_llm"] = self.llm.enabled
            result["mode"] = "llm" if self.llm.enabled else "offline"
        _set_string(result, "model", self.llm.model)
        _set_int(result, "llm_max_workers", self.llm.workers)

        _set_int(result, "max_papers", self.search.max_papers)
        _set_string(result, "search_query", self.search.query)
        if isinstance(self.search.offline, bool):
            result["use_arxiv"] = not self.search.offline
        _set_bool(result, "allow_fixture_fallback", self.search.allow_fixture_fallback)
        _set_bool(result, "strict_search", self.search.strict)

        _set_string(result, "research_mode", self.research.mode)
        _set_string(result, "research_planner", self.research.planner)
        _set_string_list(result, "research_sources", self.research.sources)
        _set_string_list(result, "research_queries", self.research.queries)
        _set_bool(result, "research_auto_query_expansion", self.research.auto_query_expansion)
        _set_int(result, "research_max_retrieval_rounds", self.research.max_retrieval_rounds)
        _set_int(result, "research_max_queries", self.research.max_queries)
        _set_string_list(result, "research_required_facets", self.research.required_facets)
        _set_bool(result, "research_use_fulltext", self.research.use_fulltext)
        _set_bool(result, "research_allow_pdf_download", self.research.allow_pdf_download)
        _set_int(result, "research_max_fulltext_documents", self.research.max_fulltext_documents)
        _set_int(result, "research_max_pdf_mb", self.research.max_pdf_mb)
        _set_bool(result, "research_keep_raw_pdf", self.research.keep_raw_pdf)
        _set_string(result, "research_parser_backend", self.research.parser_backend)
        _set_string(result, "research_read_screening", self.research.read_screening)
        _set_int(result, "research_read_batch_size", self.research.read_batch_size)
        _set_int(result, "research_read_workers", self.research.read_workers)
        _set_int(result, "research_read_min_shortlist", self.research.read_min_shortlist)
        _set_int(result, "research_read_max_shortlist", self.research.read_max_shortlist)
        _set_bool(result, "research_cache", self.research.cache)
        _set_string(result, "research_index_backend", self.research.index_backend)
        _set_path_string(result, "research_index_root", self.research.index_root)
        _set_resolved_string_list(
            result,
            "research_local_documents",
            self.research.local_documents,
            config_path,
        )

        _set_int(result, "research_max_documents", self.research.budget.max_documents)
        _set_int(result, "research_max_chunks", self.research.budget.max_chunks)
        _set_int(result, "research_max_context_tokens", self.research.budget.max_context_tokens)
        _set_int(result, "research_max_llm_calls", self.research.budget.max_llm_calls)
        _set_int(result, "research_max_follow_up_queries", self.research.budget.max_follow_up_queries)
        _set_string(result, "research_novelty_backend", self.research.budget.novelty_backend)

        _set_bool(result, "use_retrieval", self.retrieval.enabled)
        _set_int(result, "retrieval_top_k", self.retrieval.top_k)

        _set_string(result, "experiment_template", self.experiment.template)
        _set_int(result, "experiment_timeout_sec", self.experiment.timeout)
        code_task_config = _string_value(self.experiment.code_task_config)
        if code_task_config:
            result["code_task_config"] = str(_resolve_relative(config_path, code_task_config))

        _set_string(result, "task_kind", self.task.kind)
        _set_string(result, "task_name", self.task.name)
        _set_string(result, "task_objective", self.task.objective)
        _set_resolved_string(result, "task_task_file", self.task.task_file, config_path)
        _set_resolved_string(result, "task_code_root", self.task.code_root, config_path)
        _set_resolved_string(result, "task_output_root", self.task.output_root, config_path)

        _set_string(result, "implementation_mode", self.implementation.mode)
        _set_string(result, "implementation_domain_profile", self.implementation.domain_profile)
        _set_string(result, "implementation_provider", self.implementation.provider)
        _set_string(result, "implementation_agent_mode", self.implementation.agent_mode)
        _set_string(result, "implementation_agent_model", self.implementation.agent_model)
        _set_string(result, "implementation_agent_binary", self.implementation.agent_binary)
        _set_string_list(result, "implementation_agent_args", self.implementation.agent_args)
        _set_int(result, "implementation_agent_timeout_sec", self.implementation.agent_timeout_sec)
        _set_string(result, "implementation_task_handoff", self.implementation.task_handoff)
        _set_bool(result, "implementation_allow_external_agent", self.implementation.allow_external_agent)
        _set_int(result, "implementation_max_repair_attempts", self.implementation.max_repair_attempts)

        _set_string(result, "workspace_mode", self.workspace.mode)
        _set_bool(result, "workspace_reuse_source_venv", self.workspace.reuse_source_venv)
        _set_resolved_string(result, "workspace_setup_hook", self.workspace.setup_hook, config_path)
        _set_string_list(result, "workspace_include", self.workspace.include)
        _set_string_list(result, "workspace_exclude", self.workspace.exclude)

        _set_string(result, "execution_backend", self.execution.backend)
        _set_string(result, "execution_command", self.execution.command)
        _set_int(result, "execution_timeout_sec", self.execution.timeout_sec)
        _set_string(result, "execution_stream_output", self.execution.stream_output)
        _set_string(result, "execution_baseline_policy", self.execution.baseline_policy)
        _set_resolved_string(
            result,
            "execution_baseline_metrics_file",
            self.execution.baseline_metrics_file,
            config_path,
        )
        _set_bool(result, "execution_allow_dependency_install", self.execution.allow_dependency_install)

        _set_int(result, "resource_max_runtime_sec", self.resource.max_runtime_sec)
        _set_int(result, "resource_max_files", self.resource.max_files)
        _set_int(result, "resource_max_generated_lines", self.resource.max_generated_lines)
        _set_int(result, "resource_max_memory_mb", self.resource.max_memory_mb)
        _set_bool(result, "resource_allow_gpu", self.resource.allow_gpu)

        _set_string(result, "evaluation_primary_metric", self.evaluation.primary_metric)
        _set_string(result, "evaluation_direction", self.evaluation.direction)
        _set_string_list(result, "evaluation_required_metrics", self.evaluation.required_metrics)
        if self.evaluation.metric_directions:
            result["evaluation_metric_directions"] = dict(self.evaluation.metric_directions)
        _set_string_list(result, "evaluation_success_criteria", self.evaluation.success_criteria)

        _set_bool(result, "generation_enabled", self.generation.enabled)
        _set_int(result, "generation_max_batches", self.generation.max_batches)
        _set_int(result, "generation_files_per_batch", self.generation.files_per_batch)
        _set_bool(result, "generation_review_required", self.generation.review_required)
        _set_int(result, "generation_planning_review_rounds", self.generation.planning_review_rounds)
        _set_bool(result, "generation_allow_fallback_scaffold", self.generation.allow_fallback_scaffold)

        _set_string(result, "models_planner", self.models.planner)
        _set_string(result, "models_implementer", self.models.implementer)
        _set_string(result, "models_reviewer", self.models.reviewer)
        _set_string(result, "models_repairer", self.models.repairer)

        _set_string_list(result, "edit_scope_allow", self.edit_scope.allow)
        _set_string_list(result, "edit_scope_deny", self.edit_scope.deny)
        _set_bool(result, "safety_allow_large_edits", self.safety.allow_large_edits)
        _set_bool(result, "safety_require_review", self.safety.require_review)

        _apply_unified_compatibility(result)

        _set_string(result, "report_mode", self.report.mode)
        _set_string(result, "report_template", self.report.template)
        _set_string(result, "report_criteria", self.report.criteria)
        _set_string(result, "report_style", self.report.style)
        _set_string(result, "report_cost_profile", self.report.cost_profile)
        _set_string(result, "report_outline_strategy", self.report.outline_strategy)
        _set_bool(result, "report_survey_contract", self.report.survey_contract)
        _set_bool(result, "report_draft_sections", self.report.draft_sections)
        _set_bool(result, "report_debug_artifacts", self.report.debug_artifacts)
        _set_string(result, "report_agent", self.report.agent)
        _set_string(result, "report_reviewer", self.report.reviewer)
        _set_int(result, "report_max_review_iterations", self.report.max_review_iterations)
        _set_int(result, "report_max_section_tokens", self.report.max_section_tokens)
        _set_int(result, "report_max_report_tokens", self.report.max_report_tokens)
        _set_int(result, "report_max_section_sources", self.report.max_section_sources)
        _set_string(result, "report_source_strategy", self.report.source_strategy)
        _set_int(result, "report_source_batch_size", self.report.source_batch_size)
        _set_int(result, "report_max_source_batches", self.report.max_source_batches)
        _set_bool(result, "report_review_source_batches", self.report.review_source_batches)
        _set_string(result, "report_review_trace", self.report.review_trace)
        _set_string(result, "report_output_mode", self.report.output_mode)
        _set_string(result, "report_output_label", self.report.output_label)
        _set_bool(result, "report_allow_source_backtracking", self.report.allow_source_backtracking)
        _set_int(result, "report_max_backtracking_calls", self.report.max_backtracking_calls)
        _set_int(result, "report_max_backtracking_tokens", self.report.max_backtracking_tokens)
        _set_report_figures(result, self.report.figures)
        _set_report_audit(result, self.report.audit)

        if "code_task_config" not in result and _contains_code_task_config(raw_data):
            result["code_task_config"] = str(config_path.resolve())
        result["task_config"] = unified_task_config_from_runtime(result).to_json()
        return result


def load_pipeline_run_config(config_path: str | None) -> dict[str, object]:
    """Load a TOML config for ``simple-ar run`` or ``simple-ar resume``.

    Args:
        config_path: Optional TOML config path.

    Returns:
        Flat runtime configuration values understood by ``cli.py`` and
        ``stage_handlers.py``. The function intentionally keeps CLI override
        merging outside this module so command behavior stays explicit.
    """
    if not config_path:
        return {}
    path = Path(_portable_path_string(config_path))
    data = _load_toml(path)
    try:
        parsed = PipelineRunConfig.model_validate(data)
    except ValidationError as exc:
        raise RunConfigError(f"Invalid run config {path}: {exc}") from exc
    return parsed.flatten(config_path=path, raw_data=data)


def _load_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except FileNotFoundError as exc:
        raise RunConfigError(f"Run config file not found: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise RunConfigError(f"Could not parse run config {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise RunConfigError(f"Expected TOML table in run config: {path}")
    return data


def _set_string(result: dict[str, object], key: str, value: object) -> None:
    text = _string_value(value)
    if text:
        result[key] = text


def _set_path_string(result: dict[str, object], key: str, value: object) -> None:
    text = _string_value(value)
    if text:
        result[key] = _portable_path_string(text)


def _string_value(value: object) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _portable_path_string(value: str) -> str:
    """Normalize user-facing path separators for POSIX/Windows portability."""

    return value.replace("\\", "/")


def _set_int(result: dict[str, object], key: str, value: object) -> None:
    if isinstance(value, bool):
        return
    if isinstance(value, int):
        result[key] = value


def _set_bool(result: dict[str, object], key: str, value: object) -> None:
    if isinstance(value, bool):
        result[key] = value


def _set_string_list(result: dict[str, object], key: str, value: object) -> None:
    if not isinstance(value, list):
        return
    items = [item.strip() for item in (str(item) for item in value) if item.strip()]
    if items:
        result[key] = items


def _set_resolved_string(
    result: dict[str, object],
    key: str,
    value: object,
    config_path: Path,
) -> None:
    text = _string_value(value)
    if text:
        result[key] = str(_resolve_relative(config_path, text))


def _set_resolved_string_list(
    result: dict[str, object],
    key: str,
    value: object,
    config_path: Path,
) -> None:
    if not isinstance(value, list):
        return
    paths = [
        str(_resolve_relative(config_path, item.strip()))
        for item in (str(item) for item in value)
        if item.strip()
    ]
    if paths:
        result[key] = paths


def _set_report_audit(result: dict[str, object], value: object) -> None:
    if not isinstance(value, dict):
        return
    for key, enabled in value.items():
        if isinstance(enabled, bool):
            result[f"report_audit_{key}"] = enabled


def _set_report_figures(result: dict[str, object], value: object) -> None:
    if not isinstance(value, dict):
        return
    for key, setting in value.items():
        normalized = str(key).strip().lower().replace("-", "_")
        if normalized not in {"enabled", "max_figures", "format", "mode"}:
            continue
        target = f"report_figures_{normalized}"
        if isinstance(setting, bool):
            result[target] = setting
        elif isinstance(setting, int) and not isinstance(setting, bool):
            result[target] = setting
        elif isinstance(setting, str) and setting.strip():
            result[target] = setting.strip()


def _apply_unified_compatibility(result: dict[str, object]) -> None:
    """Expose unified task settings through older code-task keys.

    The goal is one user-facing config shape, while older execution code keeps
    running until it is fully migrated.
    """
    _copy_if_missing(result, "task_code_root", "code_task_code_root")
    _copy_if_missing(result, "task_task_file", "code_task_task_file")
    _copy_if_missing(result, "task_name", "code_task_name")
    _copy_if_missing(result, "execution_command", "code_task_benchmark_command")
    _copy_if_missing(result, "execution_timeout_sec", "experiment_timeout_sec")
    _copy_if_missing(result, "workspace_mode", "code_task_workspace_mode")
    _copy_if_missing(result, "workspace_reuse_source_venv", "code_task_workspace_reuse_source_venv")
    _copy_if_missing(result, "workspace_setup_hook", "code_task_workspace_setup_hook")
    _copy_if_missing(result, "workspace_include", "code_task_workspace_include")
    _copy_if_missing(result, "workspace_exclude", "code_task_workspace_exclude")
    _copy_if_missing(result, "evaluation_primary_metric", "code_task_primary_metric")
    _copy_if_missing(result, "evaluation_metric_directions", "code_task_metric_directions")
    _copy_if_missing(result, "resource_max_runtime_sec", "code_task_timeout_sec")
    _copy_if_missing(result, "execution_baseline_policy", "code_task_baseline_policy")
    _copy_if_missing(result, "execution_baseline_metrics_file", "code_task_baseline_metrics_file")

    if "experiment_template" not in result and (
        result.get("task_kind") == "existing_project" or "code_task_code_root" in result
    ):
        result["experiment_template"] = "code_task_project"


def _copy_if_missing(result: dict[str, object], source: str, target: str) -> None:
    value = result.get(source)
    if value in (None, "", [], {}):
        return
    if target not in result:
        result[target] = value


def _contains_code_task_config(data: dict[str, Any]) -> bool:
    for section in (
        "code_task",
        "benchmark",
        "metrics",
        "environment",
        "safety",
        "edit_scope",
    ):
        value = data.get(section)
        if isinstance(value, dict) and value:
            return True
    return False


def _resolve_relative(config_path: Path, value: str) -> Path:
    path = Path(_portable_path_string(value))
    if path.is_absolute():
        return path
    return (config_path.parent / path).resolve()
