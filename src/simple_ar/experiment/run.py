from __future__ import annotations

from pathlib import Path
from typing import Any

from simple_ar.agent_backends import is_external_agent_provider
from simple_ar.core.artifacts import write_json, write_text
from simple_ar.core.pipeline import Context
from simple_ar.experiment.execution.diagnosis import (
    compact_diagnosis,
    diagnose_experiment_run,
    render_diagnosis_markdown,
)
from simple_ar.experiment.execution.guards import evaluate_result_guard
from simple_ar.code_task.generation.generated_project_repair import (
    repair_generated_project_from_guard,
    repair_generated_project_with_agent_backend,
)
from simple_ar.experiment.execution.results import (
    build_canonical_results,
    load_optional_json,
    write_canonical_results,
)
from simple_ar.experiment.rerun import preserve_stage_outputs
from simple_ar.experiment.runner import run_experiment
from simple_ar.experiment.service import load_experiment_script_path
from simple_ar.experiment.stage_common import design_json, experiment_timeout, relative_or_string
from simple_ar.pipeline_stages.common import _llm_client


def execute_run(ctx: Context) -> None:
    preserve_stage_outputs(
        ctx,
        artifact_paths=(
            "stdout.txt",
            "stderr.txt",
            "execution_report.json",
            "results.json",
            "guard_report.json",
            "diagnosis.json",
            "diagnosis.md",
            "repair_summary.json",
        ),
        reason="run stage rerun",
    )
    experiment_path = Path(load_experiment_script_path(ctx))
    timeout_sec = experiment_timeout(ctx)
    ctx.emit("stage_message", f"Running experiment subprocess with {timeout_sec}s timeout.")
    result = run_experiment(experiment_path, timeout_sec=timeout_sec)
    canonical, guard, diagnosis = _write_run_outputs(ctx, result)
    if _should_attempt_greenfield_repair(ctx, guard, diagnosis):
        ctx.emit("stage_message", "Guard failed; attempting one bounded greenfield repair.")
        repair_summary = _repair_generated_project(ctx, guard, diagnosis, canonical)
        if repair_summary.get("status") == "patched":
            ctx.emit("stage_message", "Repair patched generated project; rerunning experiment.")
            result = run_experiment(experiment_path, timeout_sec=timeout_sec)
            _, repaired_guard, _ = _write_run_outputs(ctx, result, repair_summary=repair_summary)
            ctx.emit(
                "stage_message",
                f"Repair rerun guard status: {repaired_guard.get('status', 'unknown')}.",
            )


def _repair_generated_project(
    ctx: Context,
    guard: dict[str, Any],
    diagnosis: dict[str, Any],
    canonical: dict[str, Any],
) -> dict[str, Any]:
    provider = str(ctx.config.get("implementation_provider") or "local").strip().lower().replace("-", "_")
    current_metrics = canonical.get("metrics", {}) if isinstance(canonical.get("metrics"), dict) else {}
    project_dir = ctx.run_dir / "06-code" / "generated_project"
    if provider != "local" and is_external_agent_provider(provider):
        ctx.emit("stage_message", f"Using `{provider}` repair backend through agent handoff.")
        return repair_generated_project_with_agent_backend(
            run_dir=ctx.run_dir,
            project_dir=project_dir,
            provider=provider,
            result_schema=design_json(ctx, "result_schema.json"),
            guard_report=guard,
            diagnosis_report=diagnosis,
            current_metrics=current_metrics,
            output_path=ctx.artifact_path("repair_summary.json"),
            client=_llm_client(ctx),
            timeout_sec=int(ctx.config.get("implementation_agent_timeout_sec") or experiment_timeout(ctx)),
            external_enabled=ctx.config.get("implementation_allow_external_agent") is True,
            agent_mode=str(ctx.config.get("implementation_agent_mode") or ""),
            agent_model=str(ctx.config.get("implementation_agent_model") or ""),
            agent_binary=str(ctx.config.get("implementation_agent_binary") or ""),
            agent_args=tuple(str(item) for item in (ctx.config.get("implementation_agent_args") or [])),
        )
    return repair_generated_project_from_guard(
        project_dir=project_dir,
        result_schema=design_json(ctx, "result_schema.json"),
        guard_report=guard,
        diagnosis_report=diagnosis,
        current_metrics=current_metrics,
        output_path=ctx.artifact_path("repair_summary.json"),
    )


def _write_run_outputs(
    ctx: Context,
    result: Any,
    *,
    repair_summary: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    write_text(ctx.artifact_path("stdout.txt"), result.stdout or "No stdout output.\n")
    write_text(ctx.artifact_path("stderr.txt"), result.stderr or "No stderr output.\n")
    write_json(ctx.artifact_path("execution_report.json"), result.to_json())
    result_schema = design_json(ctx, "result_schema.json")
    contract = design_json(ctx, "experiment_contract.json")
    resource_plan = design_json(ctx, "resource_plan.json")
    dependency_plan = design_json(ctx, "dependency_plan.json")
    code_review = load_optional_json(ctx.run_dir / "06-code" / "code_review.json")
    code_artifacts = load_optional_json(ctx.run_dir / "06-code" / "code_artifacts.json")
    review_recovery = load_optional_json(ctx.run_dir / "06-code" / "review_failure_recovery.json")
    comparisons = _code_task_comparison_projection(ctx)
    artifacts = {
        "stdout": "07-run/stdout.txt",
        "stderr": "07-run/stderr.txt",
        "execution_report": "07-run/execution_report.json",
        "experiment_script": "06-code/experiment.py",
    }
    if code_review:
        artifacts["code_review"] = "06-code/code_review.json"
    if code_artifacts:
        artifacts["code_artifacts"] = "06-code/code_artifacts.json"
    if review_recovery:
        artifacts["review_failure_recovery"] = "06-code/review_failure_recovery.json"
    if comparisons:
        artifacts["code_task_comparison"] = comparisons[0].get("source", "")
    repair_summary = repair_summary or _existing_generated_project_repair(ctx)
    canonical = build_canonical_results(
        result,
        result_schema=result_schema,
        experiment_contract=contract,
        artifacts=artifacts,
        comparisons=comparisons,
        verdicts=_result_verdicts(result.metrics, result_schema, comparisons),
    )
    if repair_summary:
        canonical["repair"] = repair_summary
    if resource_plan:
        canonical["resource_plan"] = resource_plan
    if dependency_plan:
        canonical["dependency_plan"] = dependency_plan
    if code_review:
        canonical["code_review"] = _compact_code_review(code_review)
    if code_artifacts:
        canonical["code_artifacts"] = _compact_code_artifacts(code_artifacts)
    if review_recovery:
        canonical["review_failure_recovery"] = review_recovery
    guard = evaluate_result_guard(canonical, result_schema=result_schema)
    diagnosis = diagnose_experiment_run(
        results=canonical,
        guard_report=guard,
        result_schema=result_schema,
        code_review=code_review,
        stdout_tail=result.stdout or "",
        stderr_tail=result.stderr or "",
    )
    canonical["guard"] = guard
    canonical["diagnosis"] = compact_diagnosis(diagnosis)
    if guard.get("status") == "failed":
        canonical["status"] = "failed"
    write_json(ctx.artifact_path("guard_report.json"), guard)
    write_json(ctx.artifact_path("diagnosis.json"), diagnosis)
    write_text(ctx.artifact_path("diagnosis.md"), render_diagnosis_markdown(diagnosis))
    write_canonical_results(ctx.artifact_path("results.json"), canonical)
    return canonical, guard, diagnosis


def _existing_generated_project_repair(ctx: Context) -> dict[str, Any] | None:
    """Retain repair provenance when rerunning a previously patched greenfield project."""

    summary = load_optional_json(ctx.artifact_path("repair_summary.json"))
    if str(summary.get("status") or "").strip().lower() != "patched":
        return None
    main_path = ctx.run_dir / "06-code" / "generated_project" / "main.py"
    if not main_path.is_file():
        return None
    try:
        text = main_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if "generated_experiment.runner" not in text:
        return None
    summary = dict(summary)
    summary["reused_from_prior_run"] = True
    return summary


def _compact_code_review(review: dict[str, Any]) -> dict[str, Any]:
    """Keep the review signal compact enough for report context."""
    findings = review.get("findings")
    if not isinstance(findings, list):
        findings = []
    return {
        "schema_version": review.get("schema_version", "code_review.v1"),
        "status": review.get("status", "unknown"),
        "summary": review.get("summary", {}),
        "review_contract": review.get("review_contract", {}),
        "findings": [item for item in findings if isinstance(item, dict)][:20],
    }


def _compact_code_artifacts(artifacts: dict[str, Any]) -> dict[str, Any]:
    generated = artifacts.get("generated_files")
    files = [item for item in generated if isinstance(item, dict)] if isinstance(generated, list) else []
    return {
        "schema_version": artifacts.get("schema_version", "code_artifacts.v1"),
        "entrypoint": artifacts.get("entrypoint", "experiment.py"),
        "project_dir": artifacts.get("project_dir", "generated_project"),
        "generated_files": files[:30],
    }


def _should_attempt_greenfield_repair(
    ctx: Context,
    guard: dict[str, Any],
    diagnosis: dict[str, Any],
) -> bool:
    if guard.get("status") != "failed":
        return False
    try:
        attempts = int(ctx.config.get("implementation_max_repair_attempts", 1))
    except (TypeError, ValueError):
        attempts = 1
    if attempts < 1:
        return False
    repair = diagnosis.get("repair")
    if isinstance(repair, dict) and repair.get("local_repair_supported") is False:
        return False
    return (ctx.run_dir / "06-code" / "generated_project").is_dir()


def _result_verdicts(
    metrics: dict[str, float],
    result_schema: dict[str, Any],
    comparisons: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    verdicts: list[dict[str, Any]] = []
    primary = str(result_schema.get("primary_metric") or "").strip()
    if primary and primary in metrics:
        direction = str(result_schema.get("direction") or "maximize")
        verdicts.append(
            {
                "name": "primary_metric_observed",
                "metric": primary,
                "value": metrics[primary],
                "direction": direction,
                "verdict": "observed",
            }
        )
    for comparison in comparisons:
        if comparison.get("verdict"):
            verdicts.append(
                {
                    "name": "comparison_verdict",
                    "source": comparison.get("source", ""),
                    "verdict": comparison.get("verdict", "inconclusive"),
                }
            )
    return verdicts


def _code_task_comparison_projection(ctx: Context) -> list[dict[str, Any]]:
    meta_path = ctx.run_dir / "06-code" / "code_task_experiment.json"
    meta = load_optional_json(meta_path)
    comparison_ref = meta.get("comparison")
    comparison_path: Path | None = None
    if isinstance(comparison_ref, str) and comparison_ref.strip():
        raw_path = Path(comparison_ref)
        comparison_path = raw_path if raw_path.is_absolute() else meta_path.parent / raw_path
    if comparison_path is None or not comparison_path.exists():
        run_dir_value = meta.get("code_task_run_dir")
        if isinstance(run_dir_value, str) and run_dir_value.strip():
            raw_run_dir = Path(run_dir_value)
            code_task_run_dir = raw_run_dir if raw_run_dir.is_absolute() else meta_path.parent / raw_run_dir
        else:
            code_task_run_dir = meta_path.parent / "code_task_run"
        comparison_path = code_task_run_dir / "code_task" / "run" / "comparison.json"
    comparison = load_optional_json(comparison_path)
    if not comparison:
        return []
    return [
        {
            "kind": "code_task_baseline_vs_patched",
            "source": relative_or_string(ctx.run_dir, comparison_path),
            "verdict": comparison.get("verdict", "inconclusive"),
            "status": comparison.get("status", "unknown"),
            "metric_config": comparison.get("metric_config", {}),
            "deltas": comparison.get("deltas", {}),
            "metrics": comparison.get("metrics", []),
            "baseline": comparison.get("baseline", {}),
            "patched": comparison.get("patched", {}),
        }
    ]
