from __future__ import annotations

from pathlib import Path
from typing import Any

from simple_ar.artifacts import read_json, read_text, write_text
from simple_ar.code_task.state import (
    code_task_paths,
    load_code_task_manifest,
    manifest_section,
    save_code_task_manifest,
    utcnow_iso,
)


def write_code_task_summary(run_dir: Path) -> Path:
    """Write a human-readable summary for the current code-task state.

    Args:
        run_dir: Code-task run directory.

    Returns:
        Path to ``code_task/summary.md``.
    """
    manifest = load_code_task_manifest(run_dir)
    paths = code_task_paths(run_dir)
    summary_path = paths.task_dir / "summary.md"

    task = _read_optional(paths.task_dir / "task.md")
    patch_plan = _read_optional(paths.task_dir / "patch_plan.md")
    patch_diff = _read_optional(paths.task_dir / "patch.diff")
    validation = _read_optional_json(paths.meta_dir / "validation_report.json")
    execution = _read_optional_json(paths.run_artifact_dir / "execution_report.json")
    metrics = _read_optional_json(paths.run_artifact_dir / "metrics.json")
    failure = _read_optional(paths.run_artifact_dir / "failure_analysis.md")

    write_text(
        summary_path,
        _render_summary(
            manifest=manifest,
            task=task,
            patch_plan=patch_plan,
            patch_diff=patch_diff,
            validation=validation,
            execution=execution,
            metrics=metrics,
            failure=failure,
        ),
    )
    _update_manifest(run_dir, manifest)
    return summary_path


def _render_summary(
    *,
    manifest: dict[str, Any],
    task: str,
    patch_plan: str,
    patch_diff: str,
    validation: dict[str, Any],
    execution: dict[str, Any],
    metrics: dict[str, Any],
    failure: str,
) -> str:
    changed_files = _changed_files(manifest)
    lines = [
        "# Code Task Summary",
        "",
        f"Status: `{manifest.get('status', 'unknown')}`",
        "",
        "## Task",
        "",
        _clip(_strip_heading(task), max_chars=1200) or "No task text was recorded.",
        "",
        "## Plan",
        "",
        _plan_status(manifest, patch_plan),
        "",
        "## Patch",
        "",
        _patch_summary(manifest, patch_diff, changed_files),
        "",
        "## Validation",
        "",
        _validation_summary(validation),
        "",
        "## Benchmark",
        "",
        _execution_summary(execution, metrics),
    ]
    if failure:
        lines.extend(
            [
                "",
                "## Failure Analysis",
                "",
                _clip(_strip_heading(failure), max_chars=1800),
            ]
        )
    lines.extend(
        [
            "",
            "## Artifacts",
            "",
            "- Workspace: `code_task/workspace`",
            "- Patch plan: `code_task/patch_plan.md`",
            "- Patch diff: `code_task/patch.diff`",
            "- Validation report: `code_task/meta/validation_report.json`",
            "- Execution report: `code_task/run/execution_report.json`",
            "- Stdout/stderr: `code_task/run/stdout.txt`, `code_task/run/stderr.txt`",
            "",
        ]
    )
    return "\n".join(lines)


def _plan_status(manifest: dict[str, Any], patch_plan: str) -> str:
    plan = manifest.get("plan", {})
    status = plan.get("status", "not_started") if isinstance(plan, dict) else "not_started"
    mode = plan.get("mode", "unknown") if isinstance(plan, dict) else "unknown"
    selected = plan.get("selected_files", []) if isinstance(plan, dict) else []
    lines = [f"- Status: `{status}`", f"- Mode: `{mode}`"]
    if isinstance(selected, list) and selected:
        lines.append("- Context files: " + ", ".join(f"`{path}`" for path in selected))
    if patch_plan:
        lines.append("- Plan file exists: `code_task/patch_plan.md`")
    return "\n".join(lines)


def _patch_summary(
    manifest: dict[str, Any],
    patch_diff: str,
    changed_files: list[str],
) -> str:
    patch = manifest.get("patch", {})
    status = patch.get("status", "not_started") if isinstance(patch, dict) else "not_started"
    lines = [f"- Status: `{status}`"]
    if changed_files:
        lines.append("- Changed files: " + ", ".join(f"`{path}`" for path in changed_files))
    if patch_diff:
        added = sum(
            1
            for line in patch_diff.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        removed = sum(
            1
            for line in patch_diff.splitlines()
            if line.startswith("-") and not line.startswith("---")
        )
        lines.append(f"- Diff size: `{added}` added line(s), `{removed}` removed line(s)")
    return "\n".join(lines)


def _validation_summary(validation: dict[str, Any]) -> str:
    if not validation:
        return "- No validation report has been written yet."
    lines = [
        f"- Status: `{validation.get('status', 'unknown')}`",
        f"- Errors: `{validation.get('error_count', 0)}`",
        f"- Warnings: `{validation.get('warning_count', 0)}`",
    ]
    issues = validation.get("issues", [])
    if isinstance(issues, list) and issues:
        lines.append("- First issues:")
        for issue in issues[:5]:
            if isinstance(issue, dict):
                lines.append(
                    f"  - `{issue.get('severity', 'issue')}` "
                    f"`{issue.get('code', '')}` in `{issue.get('path', '')}`: "
                    f"{issue.get('message', '')}"
                )
    return "\n".join(lines)


def _execution_summary(execution: dict[str, Any], metrics: dict[str, Any]) -> str:
    if not execution:
        return "- Benchmark has not been executed yet."
    lines = [
        f"- Status: `{execution.get('status', 'unknown')}`",
        f"- Return code: `{execution.get('returncode')}`",
        f"- Timed out: `{execution.get('timed_out')}`",
        f"- Command: `{execution.get('command_text', '')}`",
        f"- Duration: `{execution.get('duration_sec')}` second(s)",
    ]
    if metrics:
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("| --- | ---: |")
        for key, value in sorted(metrics.items()):
            lines.append(f"| `{key}` | {value} |")
    return "\n".join(lines)


def _changed_files(manifest: dict[str, Any]) -> list[str]:
    patch = manifest.get("patch", {})
    if isinstance(patch, dict):
        value = patch.get("changed_files")
        if isinstance(value, list):
            return [str(item) for item in value if isinstance(item, str)]
    return []


def _strip_heading(text: str) -> str:
    lines = []
    for line in text.strip().splitlines():
        if line.startswith("# "):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _read_optional(path: Path) -> str:
    return read_text(path) if path.exists() else ""


def _read_optional_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    value = read_json(path)
    return value if isinstance(value, dict) else {}


def _clip(text: str, *, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 18].rstrip() + "... [truncated]"


def _update_manifest(run_dir: Path, manifest: dict[str, Any]) -> None:
    layout = manifest_section(manifest, "layout")
    layout["summary"] = "code_task/summary.md"
    manifest["layout"] = layout
    manifest["summary"] = {
        "generated_at": utcnow_iso(),
        "path": "code_task/summary.md",
    }
    save_code_task_manifest(run_dir, manifest)
