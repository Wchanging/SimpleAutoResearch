from __future__ import annotations

from typing import Any

from simple_ar.core.artifacts import write_json
from simple_ar.core.pipeline import Context
from simple_ar.experiment.code_task_bridge import (
    CODE_TASK_PROJECT_TEMPLATE,
    code_task_experiment_spec,
    is_code_task_experiment_template,
)
from simple_ar.experiment.code_task_bridge.design import resolve_code_task_design_task
from simple_ar.experiment.coding import implementation_route
from simple_ar.experiment.contracts import (
    build_experiment_design_package,
    write_experiment_design_package,
)
from simple_ar.experiment.stage_common import (
    experiment_template,
    experiment_timeout,
    greenfield_metrics,
    repo_root,
)
from simple_ar.research.outputs.artifacts import (
    SYNTHESIS_BRIEF_JSON,
    SYNTHESIS_EVIDENCE_PACK_JSON,
    SYNTHESIS_IDEA_CANDIDATES,
    SYNTHESIS_NOVELTY_CHECKS,
    write_design_handoff_artifacts,
)
from simple_ar.research.service import load_hypothesis_markdown
from simple_ar.pipeline_stages.common import (
    _downstream_source_plan,
    _list_value,
    _read_jsonl_artifact,
    _relative_artifact,
    _safe_read_json_artifact,
)


def execute_design(ctx: Context) -> None:
    hypothesis = load_hypothesis_markdown(ctx)
    _write_design_handoff(ctx)
    template = experiment_template(ctx)
    if is_code_task_experiment_template(template):
        _write_code_task_design(ctx, template=template, hypothesis=hypothesis)
        return
    if implementation_route(ctx.config, {"template": template}) == "greenfield":
        _write_greenfield_design(ctx, template=template, hypothesis=hypothesis)
        return
    _write_template_design(ctx, template=template, hypothesis=hypothesis)


def _write_code_task_design(ctx: Context, *, template: str, hypothesis: str) -> None:
    spec = code_task_experiment_spec(repo_root(), ctx.config)
    task_file, task_source, task_generation = resolve_code_task_design_task(ctx, spec)
    is_generic = spec.template == CODE_TASK_PROJECT_TEMPLATE
    code_task_contract = {
        "code_root": str(spec.code_root),
        "task_file": str(task_file),
        "benchmark_command": spec.benchmark_command,
        "primary_metric": spec.primary_metric,
        "metric_directions": dict(spec.metric_directions),
        "workspace_mode": spec.workspace_mode,
        "env_mode": spec.env_mode,
    }
    _write_v25_design_package(ctx, hypothesis=hypothesis, template=template, code_task=code_task_contract)
    write_json(
        ctx.artifact_path("experiment_plan.json"),
        {
            "name": spec.name or spec.template,
            "template": spec.template,
            "mode": "embedded_code_task",
            "hypothesis": hypothesis.strip(),
            "dataset": str(spec.code_root),
            "baseline": "existing_codebase",
            "method": "llm_planned_controlled_patch",
            "metrics": [
                "benchmark_passed",
                "benchmark_returncode",
                "benchmark_timed_out",
                "changed_files",
                "llm_patch_applied",
                "comparison_improved",
                "primary_metric_delta",
            ],
            "timeout_sec": experiment_timeout(ctx),
            "code_task": {
                "code_root": str(spec.code_root),
                "task_file": str(task_file),
                "task_source": task_source,
                "generated_task_file": _relative_artifact(ctx, task_file)
                if task_source in {"generated_from_research", "merged_user_and_research"}
                else None,
                "task_generation": task_generation,
                "benchmark_command": spec.benchmark_command,
                "config_path": spec.config_path,
                "primary_metric": spec.primary_metric,
                "metric_directions": spec.metric_directions,
                "env_mode": spec.env_mode,
                "python_executable": spec.python_executable,
                "workspace_mode": spec.workspace_mode,
                "workspace_include": list(spec.workspace_include),
                "workspace_exclude": list(spec.workspace_exclude),
                "workspace_reuse_source_venv": spec.workspace_reuse_source_venv,
                "workspace_setup_hook": spec.workspace_setup_hook,
                "max_file_bytes": spec.max_file_bytes,
                "approval": "auto_approved_inside_isolated_pipeline_workspace",
                "allow_test_changes": spec.allow_test_changes,
                "scope": "user_project" if is_generic else "bundled_demo",
            },
        },
    )


def _write_greenfield_design(ctx: Context, *, template: str, hypothesis: str) -> None:
    _write_v25_design_package(ctx, hypothesis=hypothesis, template=template, code_task=None)
    write_json(
        ctx.artifact_path("experiment_plan.json"),
        {
            "name": str(ctx.config.get("task_name") or "greenfield-experiment"),
            "template": template,
            "mode": "greenfield",
            "hypothesis": hypothesis.strip(),
            "dataset": "generated_or_configured_local_inputs",
            "baseline": "none",
            "method": "contract_bounded_greenfield_project",
            "metrics": greenfield_metrics(ctx),
            "timeout_sec": experiment_timeout(ctx),
            "implementation": {
                "mode": "generate_project",
                "project_dir": "06-code/generated_project",
                "entrypoint": "python main.py",
                "provider": str(ctx.config.get("implementation_provider") or "local"),
            },
        },
    )


def _write_template_design(ctx: Context, *, template: str, hypothesis: str) -> None:
    _write_v25_design_package(ctx, hypothesis=hypothesis, template=template, code_task=None)
    write_json(
        ctx.artifact_path("experiment_plan.json"),
        {
            "name": "toy_text_classification",
            "template": template,
            "hypothesis": hypothesis.strip(),
            "dataset": "built_in_toy_spam",
            "baseline": "keyword_rules",
            "method": "bag_of_words_logistic_regression",
            "metrics": ["accuracy", "precision", "recall"],
            "timeout_sec": experiment_timeout(ctx),
        },
    )


def _write_v25_design_package(
    ctx: Context,
    *,
    hypothesis: str,
    template: str,
    code_task: dict[str, Any] | None,
) -> None:
    package = build_experiment_design_package(
        ctx.config,
        topic=ctx.topic,
        hypothesis=hypothesis,
        template=template,
        code_task=code_task,
    )
    write_experiment_design_package(ctx.stage_dir(), package)
    ctx.emit(
        "stage_message",
        f"Wrote experiment contract package ({package.validation.status}).",
        experiment_contract="experiment_contract.json",
        result_schema="result_schema.json",
    )


def _write_design_handoff(ctx: Context) -> None:
    evidence_pack = _safe_read_json_artifact(ctx, SYNTHESIS_EVIDENCE_PACK_JSON)
    synthesis_brief = _safe_read_json_artifact(ctx, SYNTHESIS_BRIEF_JSON)
    if not evidence_pack and synthesis_brief:
        evidence_pack = _evidence_pack_from_synthesis_brief(synthesis_brief)
    if not evidence_pack:
        return
    source_plan = _downstream_source_plan(ctx)
    budget = source_plan.get("budget") if isinstance(source_plan, dict) else {}
    compact_artifacts = ctx.config.get("debug_artifacts") is not True
    if isinstance(budget, dict) and "compact_artifacts" in budget:
        compact_artifacts = bool(budget.get("compact_artifacts"))
    meta = write_design_handoff_artifacts(
        stage_dir=ctx.stage_dir(),
        evidence_pack=evidence_pack,
        idea_candidates=_idea_candidates_for_design(ctx, synthesis_brief),
        novelty_checks=_novelty_checks_for_design(ctx, synthesis_brief),
        compact_artifacts=compact_artifacts,
    )
    ctx.emit(
        "stage_message",
        "Built design experiment contract from synthesized evidence.",
        experiment_contract=meta.get("experiment_contract", ""),
    )


def _evidence_pack_from_synthesis_brief(brief: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "synthesis_brief_handoff.v1",
        "topic": brief.get("topic"),
        "source_plan": brief.get("source_plan", {}),
        "counts": brief.get("counts", {}),
        "coverage": brief.get("coverage", {}),
        "provenance": brief.get("provenance", {}),
        "papers": [
            {
                "id": row.get("paper_id"),
                "title": row.get("title"),
                "source": row.get("source"),
            }
            for row in _list_value(brief.get("paper_briefs"))
            if isinstance(row, dict)
        ],
        "paper_cards": [],
        "claim_cards": [],
        "method_cards": [],
        "dataset_cards": [],
        "code_links": [],
        "limitations": _list_value(brief.get("limitations")),
    }


def _idea_candidates_for_design(ctx: Context, synthesis_brief: dict[str, Any]) -> list[dict[str, Any]]:
    if synthesis_brief:
        ideas = _list_value(synthesis_brief.get("idea_candidates"))
        if ideas:
            return [row for row in ideas if isinstance(row, dict)]
    return _read_jsonl_artifact(ctx, SYNTHESIS_IDEA_CANDIDATES)


def _novelty_checks_for_design(ctx: Context, synthesis_brief: dict[str, Any]) -> list[dict[str, Any]]:
    if synthesis_brief:
        checks = _list_value(synthesis_brief.get("novelty_checks"))
        if checks:
            return [row for row in checks if isinstance(row, dict)]
    return _read_jsonl_artifact(ctx, SYNTHESIS_NOVELTY_CHECKS)
