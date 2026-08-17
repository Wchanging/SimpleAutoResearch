from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from simple_ar.code_task.analysis.index import build_codebase_index
from simple_ar.code_task.editing.scope import is_protected_edit_path
from simple_ar.core.artifacts import read_json, read_text, write_json, write_text
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
    _handle_llm_failure,
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
    bridge = _research_code_bridge_context(ctx)
    if bridge["markdown"]:
        synthesis = _append_bridge_context(synthesis, bridge["markdown"])
        hypothesis = _append_bridge_context(hypothesis, bridge["markdown"])
    codebase_summary = _codebase_design_summary(spec.code_root)
    source_artifacts = [
        "user_task.md",
        "goal.md",
        "problem.md",
        "synthesis.md",
        "hypothesis.md",
        *bridge["source_artifacts"],
    ]
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
                    "research_code_bridge": bridge["metadata"],
                    "codebase_summary": codebase_summary,
                }
        except LLMError as exc:
            _handle_llm_failure(ctx, "LLM task merge failed", exc)

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
        "research_code_bridge": bridge["metadata"],
        "codebase_summary": codebase_summary,
    }


def _generate_code_task_design_markdown(ctx: Context, spec: Any) -> tuple[str, dict[str, Any]]:
    goal = _safe_read_artifact(ctx, "goal.md")
    problem = _safe_read_artifact(ctx, "problem.md")
    synthesis = _safe_read_artifact(ctx, "synthesis.md")
    hypothesis = _safe_read_artifact(ctx, "hypothesis.md")
    bridge = _research_code_bridge_context(ctx)
    if bridge["markdown"]:
        synthesis = _append_bridge_context(synthesis, bridge["markdown"])
        hypothesis = _append_bridge_context(hypothesis, bridge["markdown"])
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
                    "source_artifacts": [
                        "goal.md",
                        "problem.md",
                        "synthesis.md",
                        "hypothesis.md",
                        *bridge["source_artifacts"],
                    ],
                    "research_code_bridge": bridge["metadata"],
                    "codebase_summary": codebase_summary,
                }
        except LLMError as exc:
            _handle_llm_failure(ctx, "LLM code-task design failed", exc)

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
        "source_artifacts": [
            "goal.md",
            "problem.md",
            "synthesis.md",
            "hypothesis.md",
            *bridge["source_artifacts"],
        ],
        "research_code_bridge": bridge["metadata"],
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


def _research_code_bridge_context(ctx: Context) -> dict[str, Any]:
    """Build a compact implementation handoff from research/design artifacts."""

    artifacts: list[str] = []
    sections: list[str] = []
    synthesis_brief = _read_run_json(ctx, "04-synthesize/synthesis_brief.json")
    if synthesis_brief:
        artifacts.append("04-synthesize/synthesis_brief.json")
        sections.extend(_synthesis_bridge_sections(synthesis_brief))

    contract = _read_run_json(ctx, "05-design/experiment_contract.json")
    if not contract:
        contract = _read_run_json(ctx, "05-design/evidence/experiment_contract.json")
        if contract:
            artifacts.append("05-design/evidence/experiment_contract.json")
    else:
        artifacts.append("05-design/experiment_contract.json")
    if contract:
        sections.extend(_contract_bridge_sections(contract))

    result_schema = _read_run_json(ctx, "05-design/result_schema.json")
    if result_schema:
        artifacts.append("05-design/result_schema.json")
        sections.extend(_result_schema_bridge_sections(result_schema))

    resource_plan = _read_run_json(ctx, "05-design/resource_plan.json")
    if resource_plan:
        artifacts.append("05-design/resource_plan.json")
        sections.extend(_resource_bridge_sections(resource_plan))

    if not sections:
        return {"markdown": "", "source_artifacts": [], "metadata": {"status": "empty"}}
    markdown = "## Research-to-Code Bridge\n\n" + "\n\n".join(sections)
    return {
        "markdown": markdown[:5000],
        "source_artifacts": artifacts,
        "metadata": {
            "status": "available",
            "source_artifacts": artifacts,
            "section_count": len(sections),
        },
    }


def _append_bridge_context(text: str, bridge_markdown: str) -> str:
    body = text.strip()
    if not body:
        return bridge_markdown
    return body + "\n\n" + bridge_markdown


def _read_run_json(ctx: Context, relative_path: str) -> dict[str, Any]:
    path = ctx.run_dir / relative_path
    if not path.exists():
        return {}
    try:
        data = read_json(path)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _synthesis_bridge_sections(brief: dict[str, Any]) -> list[str]:
    sections: list[str] = []
    themes = _string_list(brief.get("themes"))[:5]
    if themes:
        sections.append("### Method Transfer Signals\n" + _bullets(themes))
    gaps = _string_list(brief.get("gaps"))[:5]
    if gaps:
        sections.append("### Research Gaps To Address\n" + _bullets(gaps))
    limitations = _string_list(brief.get("limitations"))[:5]
    if limitations:
        sections.append("### Evidence Boundaries And Risks\n" + _bullets(limitations))
    ideas = _list_of_dicts(brief.get("idea_candidates"))[:4]
    if ideas:
        rows = []
        for idea in ideas:
            name = _first_text(idea.get("name"), idea.get("idea_id"), "idea")
            change = _first_text(
                idea.get("proposed_change"),
                idea.get("hypothesis"),
                idea.get("synthesis_hint"),
                "",
            )
            risk = "; ".join(_string_list(idea.get("risks"))[:2])
            rows.append(f"- `{name}`: {change[:240]}" + (f" Risk: {risk[:180]}" if risk else ""))
        sections.append("### Implementation Hypotheses\n" + "\n".join(rows))
    return sections


def _contract_bridge_sections(contract: dict[str, Any]) -> list[str]:
    sections: list[str] = []
    objective = _first_text(contract.get("objective"), contract.get("hypothesis"), contract.get("summary"), "")
    if objective:
        sections.append("### Experiment Objective\n" + f"- {objective[:500]}")
    variables = _string_list(contract.get("variables"))[:6]
    if variables:
        sections.append("### Variables Or Ablations\n" + _bullets(variables))
    risks = _string_list(contract.get("risks"))[:5]
    if risks:
        sections.append("### Design Risks\n" + _bullets(risks))
    outputs = _string_list(contract.get("expected_outputs"))[:6]
    if outputs:
        sections.append("### Required Outputs\n" + _bullets(outputs))
    return sections


def _result_schema_bridge_sections(schema: dict[str, Any]) -> list[str]:
    primary = _first_text(schema.get("primary_metric"), schema.get("primary"), "")
    required = _string_list(schema.get("required_metrics"))[:8]
    direction = _first_text(schema.get("direction"), schema.get("primary_direction"), "")
    lines = []
    if primary:
        lines.append(f"- Primary metric: `{primary}`" + (f" ({direction})" if direction else ""))
    if required:
        lines.append("- Required metrics: " + ", ".join(f"`{item}`" for item in required))
    return ["### Metric Contract\n" + "\n".join(lines)] if lines else []


def _resource_bridge_sections(plan: dict[str, Any]) -> list[str]:
    fields = []
    for key in ("max_runtime_sec", "max_memory_mb", "hardware", "device", "notes"):
        value = plan.get(key)
        if value not in (None, "", [], {}):
            fields.append(f"- {key}: {value}")
    return ["### Resource Constraints\n" + "\n".join(fields[:6])] if fields else []


def _list_of_dicts(value: object) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _string_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {item[:320]}" for item in items if item)


def _first_text(*values: object) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


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
