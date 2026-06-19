"""Public code-task API.

The package exposes the historical top-level imports through lazy loading.
This keeps downstream imports compatible while preventing small submodules
such as ``simple_ar.code_task.tools`` from importing the entire code-task
stack and creating agent-backend cycles.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any


_EXPORTS: dict[str, tuple[str, str]] = {
    "CodeTaskBatchResult": ("simple_ar.code_task.editing.attempts", "CodeTaskBatchResult"),
    "LoadedCodeTaskBatch": ("simple_ar.code_task.editing.attempts", "LoadedCodeTaskBatch"),
    "create_code_task_batch": ("simple_ar.code_task.editing.attempts", "create_code_task_batch"),
    "load_latest_code_task_batch": ("simple_ar.code_task.editing.attempts", "load_latest_code_task_batch"),
    "update_code_task_batch_state": ("simple_ar.code_task.editing.attempts", "update_code_task_batch_state"),
    "PatchPlanResult": ("simple_ar.code_task.editing.planning", "PatchPlanResult"),
    "generate_patch_plan": ("simple_ar.code_task.editing.planning", "generate_patch_plan"),
    "record_plan_decision": ("simple_ar.code_task.editing.planning", "record_plan_decision"),
    "ControlledPatchEditorBackend": ("simple_ar.code_task.editing.patching", "ControlledPatchEditorBackend"),
    "PatchApplyResult": ("simple_ar.code_task.editing.patching", "PatchApplyResult"),
    "PatchValidationError": ("simple_ar.code_task.editing.patching", "PatchValidationError"),
    "ProposedEditsResult": ("simple_ar.code_task.editing.patching", "ProposedEditsResult"),
    "apply_patch_edits": ("simple_ar.code_task.editing.patching", "apply_patch_edits"),
    "propose_patch_edits": ("simple_ar.code_task.editing.patching", "propose_patch_edits"),
    "CodeTaskEnvironmentResult": ("simple_ar.code_task.execution.environment", "CodeTaskEnvironmentResult"),
    "probe_code_task_environment": ("simple_ar.code_task.execution.environment", "probe_code_task_environment"),
    "ApplyEditRequest": ("simple_ar.code_task.editing.editor", "ApplyEditRequest"),
    "ApplyEditResult": ("simple_ar.code_task.editing.editor", "ApplyEditResult"),
    "EditRequest": ("simple_ar.code_task.editing.editor", "EditRequest"),
    "EditResult": ("simple_ar.code_task.editing.editor", "EditResult"),
    "EditorContext": ("simple_ar.code_task.editing.editor", "EditorContext"),
    "EditorSafetyPolicy": ("simple_ar.code_task.editing.editor", "EditorSafetyPolicy"),
    "EXTERNAL_AGENT_BACKEND": ("simple_ar.code_task.editing.external_agent", "EXTERNAL_AGENT_BACKEND"),
    "DEFAULT_BLOCKED_READ_PATTERNS": ("simple_ar.code_task.editing.external_agent", "DEFAULT_BLOCKED_READ_PATTERNS"),
    "SUPPORTED_EXTERNAL_AGENT_PROVIDERS": (
        "simple_ar.code_task.editing.external_agent",
        "SUPPORTED_EXTERNAL_AGENT_PROVIDERS",
    ),
    "ExternalAgentAdapterSpec": ("simple_ar.code_task.editing.external_agent", "ExternalAgentAdapterSpec"),
    "ExternalAgentConfigError": ("simple_ar.code_task.editing.external_agent", "ExternalAgentConfigError"),
    "ExternalAgentDisabledError": ("simple_ar.code_task.editing.external_agent", "ExternalAgentDisabledError"),
    "ExternalAgentEditorBackend": ("simple_ar.code_task.editing.external_agent", "ExternalAgentEditorBackend"),
    "ExternalAgentInvocationPlan": ("simple_ar.code_task.editing.external_agent", "ExternalAgentInvocationPlan"),
    "ExternalAgentPermissionPolicy": ("simple_ar.code_task.editing.external_agent", "ExternalAgentPermissionPolicy"),
    "build_external_agent_invocation_plan": (
        "simple_ar.code_task.editing.external_agent",
        "build_external_agent_invocation_plan",
    ),
    "external_agent_design_metadata": (
        "simple_ar.code_task.editing.external_agent",
        "external_agent_design_metadata",
    ),
    "is_blocked_external_agent_read_path": (
        "simple_ar.code_task.editing.external_agent",
        "is_blocked_external_agent_read_path",
    ),
    "normalize_external_agent_provider": (
        "simple_ar.code_task.editing.external_agent",
        "normalize_external_agent_provider",
    ),
    "CodeTaskExecuteResult": ("simple_ar.code_task.orchestration.execute", "CodeTaskExecuteResult"),
    "ExecuteStepRecord": ("simple_ar.code_task.orchestration.execute", "ExecuteStepRecord"),
    "execute_code_task": ("simple_ar.code_task.orchestration.execute", "execute_code_task"),
    "CodeTaskReviewResult": ("simple_ar.code_task.review", "CodeTaskReviewResult"),
    "review_code_task_changes": ("simple_ar.code_task.review", "review_code_task_changes"),
    "CodeTaskComparisonResult": ("simple_ar.code_task.execution.comparison", "CodeTaskComparisonResult"),
    "compare_code_task_runs": ("simple_ar.code_task.execution.comparison", "compare_code_task_runs"),
    "CodeTaskContextPackResult": ("simple_ar.code_task.analysis.context", "CodeTaskContextPackResult"),
    "LoadedCodeTaskContextPack": ("simple_ar.code_task.analysis.context", "LoadedCodeTaskContextPack"),
    "build_code_task_context_pack": ("simple_ar.code_task.analysis.context", "build_code_task_context_pack"),
    "load_latest_code_task_context_pack": (
        "simple_ar.code_task.analysis.context",
        "load_latest_code_task_context_pack",
    ),
    "render_prompt_context": ("simple_ar.code_task.analysis.context", "render_prompt_context"),
    "CodeTaskConfigError": ("simple_ar.code_task.runtime.config", "CodeTaskConfigError"),
    "CodeTaskExecuteOptions": ("simple_ar.code_task.runtime.config", "CodeTaskExecuteOptions"),
    "CodeTaskInitOptions": ("simple_ar.code_task.runtime.config", "CodeTaskInitOptions"),
    "load_code_task_execute_options": ("simple_ar.code_task.runtime.config", "load_code_task_execute_options"),
    "load_code_task_init_options": ("simple_ar.code_task.runtime.config", "load_code_task_init_options"),
    "parse_metric_direction_arg": ("simple_ar.code_task.runtime.config", "parse_metric_direction_arg"),
    "FailureAnalysisResult": ("simple_ar.code_task.execution.failure", "FailureAnalysisResult"),
    "analyze_code_task_failure": ("simple_ar.code_task.execution.failure", "analyze_code_task_failure"),
    "CodeTaskLocateResult": ("simple_ar.code_task.analysis.locate", "CodeTaskLocateResult"),
    "locate_code_task_context": ("simple_ar.code_task.analysis.locate", "locate_code_task_context"),
    "render_locate_summary": ("simple_ar.code_task.analysis.locate", "render_locate_summary"),
    "RepairProposalResult": ("simple_ar.code_task.execution.repair", "RepairProposalResult"),
    "propose_repair_edits": ("simple_ar.code_task.execution.repair", "propose_repair_edits"),
    "RepoMapBuildResult": ("simple_ar.code_task.analysis.repo_map", "RepoMapBuildResult"),
    "build_code_task_repo_map": ("simple_ar.code_task.analysis.repo_map", "build_code_task_repo_map"),
    "build_repo_map": ("simple_ar.code_task.analysis.repo_map", "build_repo_map"),
    "render_repo_map_summary": ("simple_ar.code_task.analysis.repo_map", "render_repo_map_summary"),
    "CodeTaskRunError": ("simple_ar.code_task.execution.runner", "CodeTaskRunError"),
    "CodeTaskRunResult": ("simple_ar.code_task.execution.runner", "CodeTaskRunResult"),
    "run_code_task_baseline": ("simple_ar.code_task.execution.runner", "run_code_task_baseline"),
    "run_code_task_benchmark": ("simple_ar.code_task.execution.runner", "run_code_task_benchmark"),
    "CodeTaskValidationResult": ("simple_ar.code_task.execution.validation", "CodeTaskValidationResult"),
    "validate_code_task": ("simple_ar.code_task.execution.validation", "validate_code_task"),
    "CodeTaskWorkPlanResult": ("simple_ar.code_task.editing.work_plan", "CodeTaskWorkPlanResult"),
    "generate_code_task_work_plan": ("simple_ar.code_task.editing.work_plan", "generate_code_task_work_plan"),
    "render_work_plan_markdown": ("simple_ar.code_task.editing.work_plan", "render_work_plan_markdown"),
    "WorkspaceModeError": ("simple_ar.code_task.workspace.modes", "WorkspaceModeError"),
    "WorkspaceResult": ("simple_ar.code_task.workspace.modes", "WorkspaceResult"),
    "WorkspaceSpec": ("simple_ar.code_task.workspace.modes", "WorkspaceSpec"),
    "CodeTaskInitResult": ("simple_ar.code_task.orchestration.workflow", "CodeTaskInitResult"),
    "initialize_code_task": ("simple_ar.code_task.orchestration.workflow", "initialize_code_task"),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    try:
        module_name, attr_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(name) from exc
    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value
