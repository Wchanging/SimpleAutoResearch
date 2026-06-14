from __future__ import annotations

from pathlib import Path
from typing import Any

from simple_ar.core.artifacts import read_json, write_json, write_text
from simple_ar.core.pipeline import Context
from simple_ar.experiment.code_task_bridge import (
    build_code_task_experiment_script,
    code_task_experiment_spec,
    is_code_task_experiment_template,
    prepare_code_task_experiment,
    write_code_task_experiment_meta,
)
from simple_ar.experiment.coding import GREENFIELD_TEMPLATE, implement_greenfield_project, implementation_route
from simple_ar.experiment.rerun import preserve_stage_outputs
from simple_ar.experiment.service import load_experiment_plan
from simple_ar.experiment.stage_common import design_json, experiment_timeout, model_name, repo_root
from simple_ar.experiment.templates import build_experiment_code
from simple_ar.pipeline_stages.common import _llm_client


def execute_code(ctx: Context) -> None:
    preserve_stage_outputs(
        ctx,
        artifact_paths=(
            "implementation_plan.json",
            "architecture_plan.json",
            "architecture_plan.md",
            "file_plan.json",
            "generated_project",
            "code_artifacts.json",
            "implementation_memory.json",
            "code_review.json",
            "code_backend.json",
            "review_failure_recovery.json",
            "experiment.py",
            "code_task_experiment.json",
            "generated_code_task.md",
            "generated_code_task_meta.json",
        ),
        reason="code stage rerun",
    )
    _enforce_experiment_contract_gate(ctx)
    plan = load_experiment_plan(ctx)
    if is_code_task_experiment_template(plan.get("template")):
        _execute_code_task_experiment_code(ctx, plan)
        return
    if implementation_route(ctx.config, plan) == "greenfield":
        _execute_greenfield_code(ctx, plan)
        return

    ctx.emit("stage_message", f"Generating experiment from template `{plan.get('template', '')}`.")
    write_text(ctx.artifact_path("experiment.py"), build_experiment_code(plan))


def _execute_greenfield_code(ctx: Context, plan: dict[str, Any]) -> None:
    ctx.emit("stage_message", "Planning and generating bounded greenfield experiment project.")
    contract = design_json(ctx, "experiment_contract.json")
    result_schema = design_json(ctx, "result_schema.json")
    resource_plan = design_json(ctx, "resource_plan.json")
    dependency_plan = design_json(ctx, "dependency_plan.json")
    domain_profile = design_json(ctx, "domain_profile.json")
    write_json(
        ctx.artifact_path("implementation_plan.json"),
        {
            "schema_version": "implementation_plan.v1",
            "mode": "greenfield",
            "template": plan.get("template", GREENFIELD_TEMPLATE),
            "contract_id": contract.get("contract_id", ""),
            "project_dir": "generated_project",
            "entrypoint": "python main.py",
            "timeout_sec": plan.get("timeout_sec") or experiment_timeout(ctx),
        },
    )
    result = implement_greenfield_project(
        stage_dir=ctx.stage_dir(),
        contract=contract,
        result_schema=result_schema,
        resource_plan=resource_plan,
        dependency_plan=dependency_plan,
        domain_profile=domain_profile,
        client=_llm_client(ctx),
    )
    if result.review_status == "failed" and ctx.config.get("use_llm") is True:
        if ctx.config.get("generation_allow_fallback_scaffold") is not True:
            ctx.emit(
                "stage_message",
                "Greenfield LLM project failed deterministic review; keeping generated artifacts for inspection.",
            )
            raise RuntimeError(f"Greenfield code review failed. See {result.code_review_path}.")
        ctx.emit(
            "stage_message",
            "Greenfield LLM project failed review; archiving it and using bounded fallback scaffold.",
        )
        archive = preserve_stage_outputs(
            ctx,
            artifact_paths=(
                "architecture_plan.json",
                "architecture_plan.md",
                "file_plan.json",
                "generated_project",
                "code_artifacts.json",
                "implementation_memory.json",
                "code_review.json",
                "code_backend.json",
                "experiment.py",
            ),
            reason="greenfield LLM review failure",
        )
        result = implement_greenfield_project(
            stage_dir=ctx.stage_dir(),
            contract=contract,
            result_schema=result_schema,
            resource_plan=resource_plan,
            dependency_plan=dependency_plan,
            domain_profile=domain_profile,
            client=None,
        )
        write_json(
            ctx.artifact_path("review_failure_recovery.json"),
            {
                "schema_version": "greenfield_review_recovery.v1",
                "reason": "llm_project_failed_code_review",
                "failed_archive_dir": archive.archive_dir.relative_to(ctx.stage_dir()).as_posix()
                if archive is not None
                else "",
                "recovery_mode": "deterministic_fallback_scaffold",
                "recovered_review_status": result.review_status,
            },
        )
    ctx.emit(
        "stage_message",
        f"Greenfield project generated at `{result.project_dir.name}` with review status {result.review_status}.",
    )
    if result.review_status == "failed":
        raise RuntimeError(f"Greenfield code review failed. See {result.code_review_path}.")


def _enforce_experiment_contract_gate(ctx: Context) -> None:
    validation_path = ctx.run_dir / "05-design" / "contract_validation.json"
    if not validation_path.is_file():
        return
    validation = read_json(validation_path)
    status = str(validation.get("status", "")).strip().lower()
    errors = [str(item) for item in validation.get("errors", []) if str(item).strip()]
    warnings = [str(item) for item in validation.get("warnings", []) if str(item).strip()]
    if status == "failed":
        detail = "\n".join(f"- {item}" for item in errors) or "- Unknown contract error."
        raise RuntimeError("Experiment contract validation failed before code generation:\n" + detail)
    for warning in warnings:
        ctx.emit("stage_message", f"Experiment contract warning: {warning}")


def _execute_code_task_experiment_code(ctx: Context, plan: dict[str, Any]) -> None:
    ctx.emit("stage_message", "Preparing embedded LLM code-task experiment.")
    spec = code_task_experiment_spec(
        repo_root(),
        ctx.config,
        task_file_override=_code_task_task_file_override(ctx, plan),
    )
    result = prepare_code_task_experiment(
        code_task_run_dir=ctx.stage_dir() / "code_task_run",
        spec=spec,
        model=model_name(ctx),
        use_llm=ctx.config.get("use_llm") is True,
        timeout_sec=int(plan.get("timeout_sec") or experiment_timeout(ctx)),
        message_callback=lambda message: ctx.emit("stage_message", message),
    )
    write_text(
        ctx.artifact_path("experiment.py"),
        build_code_task_experiment_script(
            changed_files=result.changed_files,
            timeout_sec=int(plan.get("timeout_sec") or experiment_timeout(ctx)),
        ),
    )
    write_code_task_experiment_meta(ctx.artifact_path("code_task_experiment.json"), result)


def _code_task_task_file_override(ctx: Context, plan: dict[str, Any]) -> Path | None:
    code_task = plan.get("code_task")
    if not isinstance(code_task, dict):
        return None
    generated = code_task.get("generated_task_file")
    if isinstance(generated, str) and generated.strip():
        path = Path(generated)
        return path if path.is_absolute() else ctx.run_dir / path
    if code_task.get("task_source") == "generated_from_research":
        task_file = code_task.get("task_file")
        if isinstance(task_file, str) and task_file.strip():
            path = Path(task_file)
            return path if path.is_absolute() else ctx.run_dir / path
    return None
