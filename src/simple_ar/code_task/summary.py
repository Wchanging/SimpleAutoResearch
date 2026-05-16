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
    environment = _read_optional_json(paths.meta_dir / "environment_report.json")
    validation = _read_optional_json(paths.meta_dir / "validation_report.json")
    baseline_execution = _read_run_json(paths.run_artifact_dir, "baseline", "execution_report.json")
    baseline_metrics = _read_run_json(paths.run_artifact_dir, "baseline", "metrics.json")
    patched_execution = _read_run_json(paths.run_artifact_dir, "patched", "execution_report.json")
    patched_metrics = _read_run_json(paths.run_artifact_dir, "patched", "metrics.json")
    comparison = _read_optional_json(paths.run_artifact_dir / "comparison.json")
    legacy_execution = _read_optional_json(paths.run_artifact_dir / "execution_report.json")
    legacy_metrics = _read_optional_json(paths.run_artifact_dir / "metrics.json")
    failure = _read_latest_failure(paths.run_artifact_dir, manifest)

    write_text(
        summary_path,
        _render_summary(
            manifest=manifest,
            task=task,
            patch_plan=patch_plan,
            patch_diff=patch_diff,
            environment=environment,
            validation=validation,
            baseline_execution=baseline_execution,
            baseline_metrics=baseline_metrics,
            patched_execution=patched_execution,
            patched_metrics=patched_metrics,
            comparison=comparison,
            legacy_execution=legacy_execution,
            legacy_metrics=legacy_metrics,
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
    environment: dict[str, Any],
    validation: dict[str, Any],
    baseline_execution: dict[str, Any],
    baseline_metrics: dict[str, Any],
    patched_execution: dict[str, Any],
    patched_metrics: dict[str, Any],
    comparison: dict[str, Any],
    legacy_execution: dict[str, Any],
    legacy_metrics: dict[str, Any],
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
        "## Environment",
        "",
        _environment_summary(environment, manifest),
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
        _benchmark_summary(
            baseline_execution=baseline_execution,
            baseline_metrics=baseline_metrics,
            patched_execution=patched_execution,
            patched_metrics=patched_metrics,
            comparison=comparison,
            legacy_execution=legacy_execution,
            legacy_metrics=legacy_metrics,
        ),
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
    artifact_lines = [
        "- Workspace: `code_task/workspace`",
        "- Environment report: `code_task/meta/environment_report.json`",
        "- Patch plan: `code_task/patch_plan.md`",
        "- Patch diff: `code_task/patch.diff`",
        "- Validation report: `code_task/meta/validation_report.json`",
        "- Baseline execution: `code_task/run/baseline/execution_report.json`",
        "- Patched execution: `code_task/run/patched/execution_report.json`",
        "- Stdout/stderr: `code_task/run/<label>/stdout.txt`, `code_task/run/<label>/stderr.txt`",
    ]
    if comparison:
        artifact_lines.insert(-1, "- Comparison: `code_task/run/comparison.json`")
    lines.extend(["", "## Artifacts", "", *artifact_lines, ""])
    return "\n".join(lines)


def _environment_summary(environment: dict[str, Any], manifest: dict[str, Any]) -> str:
    policy = _environment_policy(environment, manifest)
    if not environment and not policy:
        return "- Environment has not been probed yet."
    platform_data = environment.get("platform", {})
    python_data = environment.get("python", {})
    project = environment.get("project", {})
    gpu = environment.get("gpu", {})
    lines = [f"- Status: `{environment.get('status', 'not_probed')}`"]
    if policy:
        lines.append(f"- Mode: `{policy.get('mode', 'current')}`")
        lines.append(
            f"- Execution Python: `{policy.get('python_executable', 'unknown')}`"
        )
        lines.append(
            "- Dependency install: "
            f"`{policy.get('dependency_install', 'disabled')}`"
        )
    if isinstance(platform_data, dict):
        system = platform_data.get("system", "unknown")
        release = platform_data.get("release", "")
        machine = platform_data.get("machine", "")
        lines.append(f"- Platform: `{system} {release}` `{machine}`".strip())
    if isinstance(python_data, dict):
        lines.append(
            f"- Python: `{python_data.get('version', 'unknown')}` "
            f"at `{python_data.get('executable', 'unknown')}`"
        )
    if isinstance(gpu, dict):
        lines.append(f"- GPU devices: `{gpu.get('count', 0)}`")
    if isinstance(project, dict):
        dependency_files = project.get("dependency_files", [])
        test_dirs = project.get("test_dirs", [])
        if isinstance(dependency_files, list) and dependency_files:
            lines.append("- Dependency files: " + ", ".join(f"`{item}`" for item in dependency_files))
        if isinstance(test_dirs, list) and test_dirs:
            lines.append("- Test dirs: " + ", ".join(f"`{item}`" for item in test_dirs))
    warnings = environment.get("warnings", [])
    if isinstance(warnings, list) and warnings:
        lines.append("- Warnings: " + ", ".join(f"`{item}`" for item in warnings[:8]))
    return "\n".join(lines)


def _environment_policy(
    environment_report: dict[str, Any],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    environment = manifest.get("environment", {})
    if isinstance(environment, dict):
        policy = environment.get("policy")
        if isinstance(policy, dict):
            return policy
    policy = environment_report.get("execution_policy")
    return policy if isinstance(policy, dict) else {}


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


def _benchmark_summary(
    *,
    baseline_execution: dict[str, Any],
    baseline_metrics: dict[str, Any],
    patched_execution: dict[str, Any],
    patched_metrics: dict[str, Any],
    comparison: dict[str, Any],
    legacy_execution: dict[str, Any],
    legacy_metrics: dict[str, Any],
) -> str:
    sections: list[str] = []
    if baseline_execution:
        sections.extend(
            [
                "### Baseline",
                "",
                _execution_summary(baseline_execution, baseline_metrics),
            ]
        )
    if patched_execution:
        if sections:
            sections.append("")
        sections.extend(
            [
                "### Patched",
                "",
                _execution_summary(patched_execution, patched_metrics),
            ]
        )
    if comparison:
        if sections:
            sections.append("")
        sections.extend(
            [
                "### Comparison",
                "",
                _comparison_summary(comparison),
            ]
        )
    if not sections and legacy_execution:
        sections.extend(
            [
                "### Latest",
                "",
                _execution_summary(legacy_execution, legacy_metrics),
            ]
        )
    if not sections:
        return "- Benchmark has not been executed yet."
    return "\n".join(sections)


def _comparison_summary(comparison: dict[str, Any]) -> str:
    lines = [
        f"- Verdict: `{comparison.get('verdict', 'inconclusive')}`",
        f"- Status: `{comparison.get('status', 'unknown')}`",
    ]
    reasons = comparison.get("reasons", [])
    if isinstance(reasons, list) and reasons:
        lines.append("- Reasons: " + "; ".join(str(item) for item in reasons[:4]))
    rows = comparison.get("metrics", [])
    if isinstance(rows, list) and rows:
        lines.append("")
        lines.append("| Metric | Baseline | Patched | Delta | Direction |")
        lines.append("| --- | ---: | ---: | ---: | --- |")
        for row in rows:
            if not isinstance(row, dict):
                continue
            lines.append(
                "| "
                f"`{row.get('name', '')}` | "
                f"{_number_text(row.get('baseline'))} | "
                f"{_number_text(row.get('patched'))} | "
                f"{_delta_text(row.get('delta'))} | "
                f"`{row.get('interpretation', 'changed')}` |"
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
    environment = execution.get("environment", {})
    if isinstance(environment, dict) and environment:
        lines.append(f"- Environment mode: `{environment.get('mode', 'current')}`")
        lines.append(
            f"- Execution Python: `{environment.get('python_executable', 'unknown')}`"
        )
    if metrics:
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("| --- | ---: |")
        for key, value in sorted(metrics.items()):
            lines.append(f"| `{key}` | {value} |")
    return "\n".join(lines)


def _number_text(value: object) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{float(value):.6g}"
    return ""


def _delta_text(value: object) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        sign = "+" if number > 0 else ""
        return f"{sign}{number:.6g}"
    return ""


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


def _read_run_json(run_dir: Path, label: str, filename: str) -> dict[str, Any]:
    return _read_optional_json(run_dir / label / filename)


def _read_latest_failure(run_dir: Path, manifest: dict[str, Any]) -> str:
    failure = manifest.get("failure_analysis", {})
    if isinstance(failure, dict):
        path = failure.get("analysis")
        if path:
            root = run_dir.parent.parent
            return _read_optional(root / str(path))
    for label in ("patched", "baseline"):
        text = _read_optional(run_dir / label / "failure_analysis.md")
        if text:
            return text
    return _read_optional(run_dir / "failure_analysis.md")


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
