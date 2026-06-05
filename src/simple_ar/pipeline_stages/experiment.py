from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from simple_ar.core.artifacts import (
    write_json,
    write_text,
)
from simple_ar.code_task.analysis.index import build_codebase_index
from simple_ar.code_task.editing.scope import is_protected_edit_path
from simple_ar.experiment.runner import run_experiment
from simple_ar.experiment.code_task_experiment import (
    CODE_TASK_PROJECT_TEMPLATE,
    build_code_task_experiment_script,
    code_task_experiment_spec,
    is_code_task_experiment_template,
    prepare_code_task_experiment,
    write_code_task_experiment_meta,
)
from simple_ar.experiment.templates import build_experiment_code
from simple_ar.integrations.llm import LLMError
from simple_ar.core.pipeline import Context
from simple_ar.research.outputs.artifacts import (
    SYNTHESIS_EVIDENCE_PACK_JSON,
    SYNTHESIS_IDEA_CANDIDATES,
    SYNTHESIS_NOVELTY_CHECKS,
    SYNTHESIS_BRIEF_JSON,
    write_design_handoff_artifacts,
)
from simple_ar.research.prompts import (
    CODE_TASK_DESIGN_SYSTEM,
    code_task_design_user_prompt,
)
from simple_ar.research.service import load_hypothesis_markdown
from simple_ar.experiment.service import (
    load_experiment_plan,
    load_experiment_script_path,
)
from simple_ar.pipeline_stages.common import (
    _downstream_source_plan,
    _ensure_heading,
    _list_value,
    _llm_client,
    _markdown_body,
    _read_jsonl_artifact,
    _relative_artifact,
    _safe_read_artifact,
    _safe_read_json_artifact,
    _text_field,
)

def execute_design(ctx: Context) -> None:
    hypothesis = load_hypothesis_markdown(ctx)
    _write_design_handoff(ctx)
    template = _experiment_template(ctx)
    if is_code_task_experiment_template(template):
        spec = code_task_experiment_spec(_repo_root(), ctx.config)
        task_file, task_source, task_generation = _resolve_code_task_design_task(ctx, spec)
        is_generic = spec.template == CODE_TASK_PROJECT_TEMPLATE
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
                "timeout_sec": _experiment_timeout(ctx),
                "code_task": {
                    "code_root": str(spec.code_root),
                    "task_file": str(task_file),
                    "task_source": task_source,
                    "generated_task_file": _relative_artifact(ctx, task_file)
                    if task_source == "generated_from_research"
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
        return

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
            "timeout_sec": _experiment_timeout(ctx),
        },
    )

def _write_design_handoff(ctx: Context) -> None:
    """Write design-owned experiment contract and optional tool handoff artifacts."""
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
    """Return an evidence-pack-like view for design from the compact brief."""
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

def _resolve_code_task_design_task(
    ctx: Context,
    spec: Any,
) -> tuple[Path, str, dict[str, Any]]:
    """Return the task file used by an embedded code-task experiment.

    Explicit user-provided task files remain the preferred source. For generic
    8-stage code-task runs without a task file, the design stage writes a
    generated Markdown task from earlier research artifacts. That keeps the
    standalone code-task workflow strict while allowing research-first pipeline
    runs to discover and frame the code task gradually.
    """
    if spec.task_file is not None:
        return spec.task_file, "user_file", {"mode": "user_file"}
    if spec.template != CODE_TASK_PROJECT_TEMPLATE:
        raise RuntimeError(f"Missing task file for code-task template: {spec.template}")

    task_markdown, generation = _generate_code_task_design_markdown(ctx, spec)
    task_path = ctx.artifact_path("generated_code_task.md")
    write_text(task_path, task_markdown)
    write_json(ctx.artifact_path("generated_code_task_meta.json"), generation)
    ctx.emit(
        "stage_message",
        "Generated code-task task file from research artifacts because no task_file was provided.",
    )
    return task_path, "generated_from_research", generation

def _generate_code_task_design_markdown(ctx: Context, spec: Any) -> tuple[str, dict[str, Any]]:
    """Generate a conservative code-task Markdown file for a research-first run."""
    goal = _safe_read_artifact(ctx, "goal.md")
    problem = _safe_read_artifact(ctx, "problem.md")
    synthesis = _safe_read_artifact(ctx, "synthesis.md")
    hypothesis = _safe_read_artifact(ctx, "hypothesis.md")
    codebase_summary = _codebase_design_summary(spec.code_root)
    client = _llm_client(ctx)
    if client is not None:
        try:
            ctx.emit("stage_message", "Calling LLM to derive an embedded code-task from research artifacts.")
            response = client.ask_json(
                CODE_TASK_DESIGN_SYSTEM,
                code_task_design_user_prompt(
                    topic=ctx.topic,
                    goal_markdown=goal,
                    problem_markdown=problem,
                    synthesis_markdown=synthesis,
                    hypothesis_markdown=hypothesis,
                    codebase_summary_json=json.dumps(codebase_summary, indent=2, ensure_ascii=False),
                    benchmark_command=spec.benchmark_command or "",
                    primary_metric=spec.primary_metric or "",
                ),
                label="design.code_task_task",
            )
            task = _text_field(response, "task_markdown")
            if task:
                return _ensure_heading(task, "Code Task"), {
                    "mode": "llm",
                    "source_artifacts": ["goal.md", "problem.md", "synthesis.md", "hypothesis.md"],
                    "codebase_summary": codebase_summary,
                }
        except LLMError as exc:
            ctx.emit("stage_message", f"LLM code-task design failed; using deterministic fallback. {exc}")

    return _fallback_code_task_design_markdown(
        topic=ctx.topic,
        goal=goal,
        problem=problem,
        synthesis=synthesis,
        hypothesis=hypothesis,
        codebase_summary=codebase_summary,
        benchmark_command=spec.benchmark_command or "",
        primary_metric=spec.primary_metric or "",
    ), {
        "mode": "fallback",
        "source_artifacts": ["goal.md", "problem.md", "synthesis.md", "hypothesis.md"],
        "codebase_summary": codebase_summary,
    }

def _codebase_design_summary(code_root: Path) -> dict[str, Any]:
    """Build a compact codebase summary for task generation prompts."""
    try:
        index = build_codebase_index(code_root)
    except Exception as exc:
        return {
            "status": "unavailable",
            "error": str(exc),
            "code_root": str(code_root),
        }
    project = index.get("project", {})
    files = index.get("files", [])
    source_files = [
        {
            "path": item.get("path"),
            "kind": item.get("kind"),
            "role_tags": item.get("role_tags", []),
            "summary": item.get("summary", ""),
        }
        for item in files
        if isinstance(item, dict) and "test" not in set(item.get("role_tags", []))
    ][:20]
    protected_files = [
        item.get("path")
        for item in files
        if isinstance(item, dict) and is_protected_edit_path(str(item.get("path", "")))
    ][:20]
    return {
        "status": "ok",
        "code_root": str(code_root),
        "project": {
            "file_count": project.get("file_count", 0),
            "python_file_count": project.get("python_file_count", 0),
            "test_file_count": project.get("test_file_count", 0),
            "entrypoint_candidates": project.get("entrypoint_candidates", []),
            "common_imports": project.get("common_imports", [])[:10],
        },
        "source_files": source_files,
        "protected_validation_files": protected_files,
    }

def _fallback_code_task_design_markdown(
    *,
    topic: str,
    goal: str,
    problem: str,
    synthesis: str,
    hypothesis: str,
    codebase_summary: dict[str, Any],
    benchmark_command: str,
    primary_metric: str,
) -> str:
    """Create a deterministic task file when task-generation LLM calls fail."""
    project = codebase_summary.get("project", {}) if isinstance(codebase_summary, dict) else {}
    files = codebase_summary.get("source_files", []) if isinstance(codebase_summary, dict) else []
    file_lines = [
        f"- `{item.get('path')}`: {item.get('summary', '')}"
        for item in files
        if isinstance(item, dict) and item.get("path")
    ][:8]
    if not file_lines:
        file_lines = ["- Inspect the source files selected by the code-task planner."]
    metric_text = primary_metric or "the configured benchmark metrics"
    command_text = benchmark_command or "the configured benchmark command"
    context = _first_non_empty_markdown_body(hypothesis, synthesis, problem, goal)
    return (
        "# Code Task\n\n"
        "## Objective\n\n"
        f"Improve the existing codebase for the research goal `{topic}` with a small, benchmarkable patch.\n\n"
        "## Research Motivation\n\n"
        f"{context}\n\n"
        "## Target Codebase Signals\n\n"
        f"- Python files: {project.get('python_file_count', 'unknown')}\n"
        f"- Test files: {project.get('test_file_count', 'unknown')}\n"
        f"- Entrypoint candidates: {', '.join(str(item) for item in project.get('entrypoint_candidates', [])[:5]) or 'unknown'}\n"
        + "\n".join(file_lines)
        + "\n\n"
        "## Constraints\n\n"
        "- Modify implementation/source files only; do not edit tests, benchmark files, or validation targets.\n"
        "- Keep the patch small and readable.\n"
        "- Preserve public APIs unless a minimal internal API change is necessary.\n"
        "- Avoid adding heavyweight dependencies or resource-intensive training loops.\n\n"
        "## Success Criteria\n\n"
        f"- `{command_text}` completes successfully after the patch.\n"
        f"- `{metric_text}` improves or at least does not regress under the recorded metric direction.\n"
        "- The patch remains easy to review through `code_task/patch.diff`.\n\n"
        "## Suggested Investigation Steps\n\n"
        "- Inspect the codebase index and benchmark output before editing.\n"
        "- Identify the smallest source-level bottleneck or modeling weakness connected to the research synthesis.\n"
        "- Propose a controlled old/new text edit, then validate with the recorded benchmark.\n"
    )

def _first_non_empty_markdown_body(*values: str) -> str:
    """Return a compact Markdown body from the first non-empty artifact."""
    for value in values:
        body = _markdown_body(value)
        if body:
            return body[:1200]
    return "The earlier research artifacts were thin; treat this as an exploratory local improvement task."

def execute_code(ctx: Context) -> None:
    plan = load_experiment_plan(ctx)
    if is_code_task_experiment_template(plan.get("template")):
        _execute_code_task_experiment_code(ctx, plan)
        return

    ctx.emit("stage_message", f"Generating experiment from template `{plan.get('template', '')}`.")
    code = build_experiment_code(plan)
    write_text(ctx.artifact_path("experiment.py"), code)

def _execute_code_task_experiment_code(ctx: Context, plan: dict[str, Any]) -> None:
    """Prepare an embedded code-task experiment and write its run harness."""
    ctx.emit("stage_message", "Preparing embedded LLM code-task experiment.")
    spec = code_task_experiment_spec(
        _repo_root(),
        ctx.config,
        task_file_override=_code_task_task_file_override(ctx, plan),
    )
    result = prepare_code_task_experiment(
        code_task_run_dir=ctx.stage_dir() / "code_task_run",
        spec=spec,
        model=_model(ctx),
        use_llm=ctx.config.get("use_llm") is True,
        timeout_sec=int(plan.get("timeout_sec") or _experiment_timeout(ctx)),
        message_callback=lambda message: ctx.emit("stage_message", message),
    )
    write_text(
        ctx.artifact_path("experiment.py"),
        build_code_task_experiment_script(
            changed_files=result.changed_files,
            timeout_sec=int(plan.get("timeout_sec") or _experiment_timeout(ctx)),
        ),
    )
    write_code_task_experiment_meta(ctx.artifact_path("code_task_experiment.json"), result)

def _code_task_task_file_override(ctx: Context, plan: dict[str, Any]) -> Path | None:
    """Resolve a design-stage generated task file for embedded code-task runs."""
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

def execute_run(ctx: Context) -> None:
    experiment_path = Path(load_experiment_script_path(ctx))
    timeout_sec = _experiment_timeout(ctx)
    ctx.emit("stage_message", f"Running experiment subprocess with {timeout_sec}s timeout.")
    result = run_experiment(experiment_path, timeout_sec=timeout_sec)
    write_text(ctx.artifact_path("stdout.txt"), result.stdout or "No stdout output.\n")
    write_text(ctx.artifact_path("stderr.txt"), result.stderr or "No stderr output.\n")
    write_json(ctx.artifact_path("results.json"), result.to_json())

def _experiment_template(ctx: Context) -> str:
    """Read the configured experiment template name."""
    value = ctx.config.get("experiment_template", "toy_text_classification")
    template = str(value).strip()
    return template or "toy_text_classification"

def _model(ctx: Context) -> str | None:
    """Read the configured model name for helper workflows."""
    model_value = ctx.config.get("model")
    return str(model_value) if model_value else None

def _repo_root() -> Path:
    """Return the repository root for bundled examples in editable checkouts."""
    return Path(__file__).resolve().parents[2]

def _experiment_timeout(ctx: Context) -> int:
    """Read the experiment subprocess timeout with a safe default."""
    value = ctx.config.get("experiment_timeout_sec", 30)
    try:
        timeout = int(value)
    except (TypeError, ValueError):
        timeout = 30
    return min(max(1, timeout), 300)

__all__ = [
    "execute_design",
    "execute_code",
    "execute_run",
]
