from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from simple_ar.core.artifacts import read_json, write_json, write_text
from simple_ar.code_task import execute_code_task, initialize_code_task
from simple_ar.core.pipeline import Context
from simple_ar.experiment.code_task_bridge import (
    build_code_task_experiment_script,
    code_task_experiment_spec,
    is_code_task_experiment_template,
    prepare_code_task_experiment,
    write_code_task_experiment_meta,
)
from simple_ar.experiment.coding import GREENFIELD_TEMPLATE, implementation_route
from simple_ar.code_task.generation.writer import build_greenfield_harness_script
from simple_ar.experiment.rerun import preserve_stage_outputs
from simple_ar.experiment.service import load_experiment_plan
from simple_ar.experiment.stage_common import design_json, experiment_timeout, model_name, relative_or_string, repo_root
from simple_ar.experiment.templates import build_experiment_code


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
    ctx.emit("stage_message", "Planning and generating greenfield project through unified code-task engine.")
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
            "agent_mode": str(ctx.config.get("implementation_agent_mode") or ""),
        },
    )
    task_file = ctx.artifact_path("generated_code_task.md")
    write_text(
        task_file,
        _greenfield_task_markdown(
            contract=contract,
            result_schema=result_schema,
            resource_plan=resource_plan,
            dependency_plan=dependency_plan,
            domain_profile=domain_profile,
        ),
    )
    code_task_run_dir = ctx.stage_dir() / "code_task_run"
    if not (code_task_run_dir / "manifest.json").is_file():
        initialize_code_task(
            run_dir=code_task_run_dir,
            code_root=None,
            task_file=task_file,
            kind="greenfield",
            benchmark_command="python generated_project/main.py",
            workspace_mode="empty",
            env_mode="current",
            primary_metric=str(result_schema.get("primary_metric") or ""),
        )
    execute_result = execute_code_task(
        code_task_run_dir,
        to_step="review",
        model=model_name(ctx),
        use_llm=ctx.config.get("use_llm") is True,
        timeout_sec=int(plan.get("timeout_sec") or experiment_timeout(ctx)),
        max_files=int(resource_plan.get("max_files") or ctx.config.get("execute_max_files") or 8),
        max_source_chars_per_file=int(ctx.config.get("execute_max_source_chars_per_file") or 4000),
        max_generated_lines=int(resource_plan.get("max_generated_lines") or 1600),
        planning_review_rounds=int(ctx.config.get("generation_planning_review_rounds") or 2),
        repair_rounds=int(ctx.config.get("implementation_max_repair_attempts", 1) or 1),
        implementation_provider=str(ctx.config.get("implementation_provider") or "local"),
        implementation_agent_mode=str(ctx.config.get("implementation_agent_mode") or ""),
        implementation_allow_external_agent=ctx.config.get("implementation_allow_external_agent") is True,
        implementation_agent_model=str(ctx.config.get("implementation_agent_model") or ""),
        implementation_agent_binary=str(ctx.config.get("implementation_agent_binary") or ""),
        implementation_agent_args=tuple(str(item) for item in (ctx.config.get("implementation_agent_args") or [])),
        implementation_agent_timeout_sec=int(ctx.config.get("implementation_agent_timeout_sec") or 600),
        message_callback=lambda message: ctx.emit("stage_message", message),
    )
    write_json(
        ctx.artifact_path("generated_code_task_meta.json"),
        {
            "schema_version": "embedded_greenfield_code_task.v1",
            "code_task_run_dir": relative_or_string(ctx.run_dir, code_task_run_dir),
            "stop_reason": execute_result.stop_reason,
            "next_action": execute_result.next_action,
            "summary": relative_or_string(ctx.run_dir, execute_result.summary_path),
        },
    )
    if execute_result.stop_reason != "stop_point":
        raise RuntimeError(
            "Embedded greenfield code-task did not pass code review. "
            f"Stop reason: {execute_result.stop_reason}. See {execute_result.summary_path}."
        )
    _project_code_task_outputs(ctx, code_task_run_dir)
    ctx.emit(
        "stage_message",
        "Greenfield project generated through nested code-task run.",
    )


def _greenfield_task_markdown(
    *,
    contract: dict[str, Any],
    result_schema: dict[str, Any],
    resource_plan: dict[str, Any],
    dependency_plan: dict[str, Any],
    domain_profile: dict[str, Any],
) -> str:
    return (
        "# Greenfield Code Task\n\n"
        "Implement the experiment project described by the design artifacts below. "
        "Create a runnable Python project under `generated_project/` and keep it bounded, "
        "auditable, and reproducible.\n\n"
        "## Experiment Contract\n\n"
        f"```json\n{_json_block(contract)}\n```\n\n"
        "## Result Schema\n\n"
        f"```json\n{_json_block(result_schema)}\n```\n\n"
        "## Resource Plan\n\n"
        f"```json\n{_json_block(resource_plan)}\n```\n\n"
        "## Dependency Plan\n\n"
        f"```json\n{_json_block(dependency_plan)}\n```\n\n"
        "## Domain Profile\n\n"
        f"```json\n{_json_block(domain_profile)}\n```\n"
    )


def _json_block(value: dict[str, Any]) -> str:
    import json

    return json.dumps(value, indent=2, ensure_ascii=False)


def _project_code_task_outputs(ctx: Context, code_task_run_dir: Path) -> None:
    task_dir = code_task_run_dir / "code_task"
    nested_project = task_dir / "workspace" / "generated_project"
    project_dir = ctx.stage_dir() / "generated_project"
    if not nested_project.is_dir():
        raise RuntimeError(f"Nested greenfield code-task did not generate project: {nested_project}")
    if project_dir.exists():
        shutil.rmtree(project_dir)
    shutil.copytree(nested_project, project_dir)
    copies = {
        task_dir / "meta" / "architecture_plan.json": ctx.artifact_path("architecture_plan.json"),
        task_dir / "meta" / "architecture_plan.md": ctx.artifact_path("architecture_plan.md"),
        task_dir / "meta" / "file_plan.json": ctx.artifact_path("file_plan.json"),
        task_dir / "meta" / "code_artifacts.json": ctx.artifact_path("code_artifacts.json"),
        task_dir / "meta" / "review_report.json": ctx.artifact_path("code_review.json"),
        task_dir / "memory" / "implementation_memory.json": ctx.artifact_path("implementation_memory.json"),
    }
    for source, target in copies.items():
        if source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    nested_backend = {}
    nested_backend_path = task_dir / "meta" / "code_backend.json"
    if nested_backend_path.is_file():
        nested_backend = read_json(nested_backend_path)
    write_json(
        ctx.artifact_path("code_backend.json"),
        {
            "schema_version": "code_backend.v1",
            "backend": "unified_code_task_greenfield",
            "provider": "code_task",
            "code_task_run_dir": relative_or_string(ctx.run_dir, code_task_run_dir),
            "project_dir": "generated_project",
            "entrypoint": "python main.py",
            "nested_code_backend": nested_backend,
        },
    )
    write_text(ctx.artifact_path("experiment.py"), build_greenfield_harness_script())


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
        baseline_policy=str(ctx.config.get("code_task_baseline_policy") or ctx.config.get("baseline_policy") or "auto"),
        baseline_metrics_file=_optional_path_config(ctx.config.get("code_task_baseline_metrics_file") or ctx.config.get("baseline_metrics_file")),
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


def _optional_path_config(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None
