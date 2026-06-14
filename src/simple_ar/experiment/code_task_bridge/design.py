from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from simple_ar.code_task.analysis.index import build_codebase_index
from simple_ar.code_task.editing.scope import is_protected_edit_path
from simple_ar.core.artifacts import read_text, write_json, write_text
from simple_ar.core.pipeline import Context
from simple_ar.experiment.code_task_bridge.spec import CODE_TASK_PROJECT_TEMPLATE
from simple_ar.integrations.llm import LLMError
from simple_ar.research.prompts import (
    CODE_TASK_DESIGN_SYSTEM,
    code_task_design_user_prompt,
    merged_code_task_design_user_prompt,
)
from simple_ar.pipeline_stages.common import (
    _ensure_heading,
    _llm_client,
    _markdown_body,
    _safe_read_artifact,
    _text_field,
)


def resolve_code_task_design_task(ctx: Context, spec: Any) -> tuple[Path, str, dict[str, Any]]:
    """Return the task file used by an embedded code-task experiment."""

    if spec.task_file is not None:
        if _task_handoff_mode(ctx) == "merge":
            task_markdown, generation = _generate_merged_code_task_design_markdown(ctx, spec)
            task_path = ctx.artifact_path("generated_code_task.md")
            write_text(task_path, task_markdown)
            write_json(ctx.artifact_path("generated_code_task_meta.json"), generation)
            ctx.emit(
                "stage_message",
                "Merged user task file with research artifacts for embedded code-task handoff.",
            )
            return task_path, "merged_user_and_research", generation
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


def _generate_merged_code_task_design_markdown(ctx: Context, spec: Any) -> tuple[str, dict[str, Any]]:
    user_task = read_text(spec.task_file)
    goal = _safe_read_artifact(ctx, "goal.md")
    problem = _safe_read_artifact(ctx, "problem.md")
    synthesis = _safe_read_artifact(ctx, "synthesis.md")
    hypothesis = _safe_read_artifact(ctx, "hypothesis.md")
    codebase_summary = _codebase_design_summary(spec.code_root)
    source_artifacts = ["user_task.md", "goal.md", "problem.md", "synthesis.md", "hypothesis.md"]
    client = _llm_client(ctx)
    if client is not None:
        try:
            ctx.emit("stage_message", "Calling LLM to merge user task and research context.")
            response = client.ask_json(
                CODE_TASK_DESIGN_SYSTEM,
                merged_code_task_design_user_prompt(
                    topic=ctx.topic,
                    user_task_markdown=user_task,
                    goal_markdown=goal,
                    problem_markdown=problem,
                    synthesis_markdown=synthesis,
                    hypothesis_markdown=hypothesis,
                    codebase_summary_json=json.dumps(codebase_summary, indent=2, ensure_ascii=False),
                    benchmark_command=spec.benchmark_command or "",
                    primary_metric=spec.primary_metric or "",
                ),
                label="design.code_task_task_merge",
            )
            task = _text_field(response, "task_markdown")
            if task:
                return _ensure_heading(task, "Code Task"), {
                    "mode": "llm_merge",
                    "user_task_file": str(spec.task_file),
                    "source_artifacts": source_artifacts,
                    "codebase_summary": codebase_summary,
                }
        except LLMError as exc:
            ctx.emit("stage_message", f"LLM task merge failed; using deterministic fallback. {exc}")

    return _fallback_merged_code_task_design_markdown(
        topic=ctx.topic,
        user_task=user_task,
        goal=goal,
        problem=problem,
        synthesis=synthesis,
        hypothesis=hypothesis,
        codebase_summary=codebase_summary,
        benchmark_command=spec.benchmark_command or "",
        primary_metric=spec.primary_metric or "",
    ), {
        "mode": "fallback_merge",
        "user_task_file": str(spec.task_file),
        "source_artifacts": source_artifacts,
        "codebase_summary": codebase_summary,
    }


def _generate_code_task_design_markdown(ctx: Context, spec: Any) -> tuple[str, dict[str, Any]]:
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


def _fallback_merged_code_task_design_markdown(
    *,
    topic: str,
    user_task: str,
    goal: str,
    problem: str,
    synthesis: str,
    hypothesis: str,
    codebase_summary: dict[str, Any],
    benchmark_command: str,
    primary_metric: str,
) -> str:
    project = codebase_summary.get("project", {}) if isinstance(codebase_summary, dict) else {}
    files = codebase_summary.get("source_files", []) if isinstance(codebase_summary, dict) else []
    file_lines = [
        f"- `{item.get('path')}`: {item.get('summary', '')}"
        for item in files
        if isinstance(item, dict) and item.get("path")
    ][:8]
    if not file_lines:
        file_lines = ["- Inspect the source files selected by the code-task planner."]
    context = _first_non_empty_markdown_body(hypothesis, synthesis, problem, goal)
    command_text = benchmark_command or "the configured benchmark command"
    metric_text = primary_metric or "the configured benchmark metrics"
    return (
        "# Code Task\n\n"
        "## Objective\n\n"
        f"Complete the user-specified coding task for `{topic}` while using the research artifacts only as supporting context.\n\n"
        "## User Requirements\n\n"
        f"{_nested_markdown_body(user_task)}\n\n"
        "## Research-Derived Context\n\n"
        f"{context}\n\n"
        "## Target Codebase Signals\n\n"
        f"- Python files: {project.get('python_file_count', 'unknown')}\n"
        f"- Test files: {project.get('test_file_count', 'unknown')}\n"
        f"- Entrypoint candidates: {', '.join(str(item) for item in project.get('entrypoint_candidates', [])[:5]) or 'unknown'}\n"
        + "\n".join(file_lines)
        + "\n\n"
        "## Constraints\n\n"
        "- Treat the user requirements above as hard constraints.\n"
        "- Do not edit tests, benchmark files, or validation targets.\n"
        "- Keep the patch as small as possible while satisfying the task.\n"
        "- Preserve public APIs unless the user task explicitly requires an API change.\n"
        "- Avoid heavyweight dependencies or long-running training loops unless already configured.\n\n"
        "## Success Criteria\n\n"
        f"- `{command_text}` completes successfully after the patch.\n"
        f"- `{metric_text}` improves or avoids regression under the configured direction.\n"
        "- The patch remains reviewable through `code_task/patch.diff` and passes validation.\n\n"
        "## Suggested Investigation Steps\n\n"
        "- Read the user task first, then use the research context to prioritize the smallest useful change.\n"
        "- Inspect the codebase index and baseline benchmark output before editing.\n"
        "- Propose controlled edits only inside the allowed implementation scope.\n"
    )


def _codebase_design_summary(code_root: Path) -> dict[str, Any]:
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
    for value in values:
        body = _markdown_body(value)
        if body:
            return body[:1200]
    return "The earlier research artifacts were thin; treat this as an exploratory local improvement task."


def _nested_markdown_body(value: str) -> str:
    body = _markdown_body(value) or value.strip()
    if not body:
        return "(empty user task file)"
    lines: list[str] = []
    for line in body.splitlines():
        stripped = line.lstrip()
        indent = line[: len(line) - len(stripped)]
        if stripped.startswith("#"):
            hashes = len(stripped) - len(stripped.lstrip("#"))
            if hashes and hashes < 6 and stripped[hashes : hashes + 1] in {"", " "}:
                line = f"{indent}#{stripped}"
        lines.append(line)
    return "\n".join(lines).strip()


def _task_handoff_mode(ctx: Context) -> str:
    value = str(ctx.config.get("implementation_task_handoff") or "").strip().lower().replace("-", "_")
    if value in {"merge", "merged", "merge_with_research", "user_and_research"}:
        return "merge"
    return "user_file"
