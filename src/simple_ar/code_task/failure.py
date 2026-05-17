from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from simple_ar.artifacts import read_json, read_text, write_text
from simple_ar.code_task.state import (
    code_task_paths,
    is_relative_to,
    load_code_task_manifest,
    manifest_section,
    save_code_task_manifest,
    utcnow_iso,
)
from simple_ar.code_task.summary import write_code_task_summary


TRACEBACK_RE = re.compile(r"Traceback \(most recent call last\):[\s\S]*", re.MULTILINE)
FILE_LINE_RE = re.compile(r'File "([^"]+)", line (\d+)(?:, in ([^\n]+))?')
SIGNAL_LINES = (
    "AssertionError",
    "ModuleNotFoundError",
    "ImportError",
    "SyntaxError",
    "NameError",
    "TypeError",
    "ValueError",
    "FAILED",
    "ERROR",
    "Timed out",
)


@dataclass(frozen=True)
class FailureAnalysisResult:
    """Result returned after writing deterministic failure analysis.

    Args:
        run_dir: Code-task run directory.
        analysis_path: Markdown failure analysis path.
        status: ``needs_repair`` when a failed execution is present,
            otherwise ``no_failure``.
        implicated_files: Workspace-relative files mentioned by tracebacks.
        source: Evidence source used for the diagnosis.
    """

    run_dir: Path
    analysis_path: Path
    status: str
    implicated_files: tuple[str, ...]
    source: str = "benchmark"


def analyze_code_task_failure(run_dir: Path) -> FailureAnalysisResult:
    """Create a compact Markdown diagnosis from the latest benchmark run.

    Args:
        run_dir: Code-task run directory.

    Returns:
        Failure analysis metadata.

    Raises:
        FileNotFoundError: If no execution report exists.
        RuntimeError: If ``run_dir`` is not a code-task run.
    """
    manifest = load_code_task_manifest(run_dir)
    paths = code_task_paths(run_dir)
    artifacts = _latest_failure_artifacts(paths, manifest)
    report_path = artifacts.get("execution_report")
    report: dict[str, Any] = {}
    if isinstance(report_path, Path) and report_path.exists():
        report_value = read_json(report_path)
        if not isinstance(report_value, dict):
            raise RuntimeError(f"Expected JSON object in {report_path}")
        report = report_value

    stdout = _read_optional(artifacts["stdout"])
    stderr = _read_optional(artifacts["stderr"])
    validation = _read_optional_json(artifacts["validation_report"])
    if not report and not _validation_failed(validation):
        raise FileNotFoundError(
            "No failed benchmark execution or validation report was found. "
            "Run `simple-ar code-task validate` or `simple-ar code-task run` first."
        )

    traceback_block = _traceback_block(stderr)
    implicated_files = _dedupe(
        _implicated_files(traceback_block or stderr, paths.workspace_dir)
        + _validation_issue_files(validation, paths.workspace_dir)
    )
    signal_lines = _signal_lines(stdout, stderr) or _validation_signal_lines(validation)
    changed_files = _changed_files(manifest)

    status = "no_failure" if report.get("status") == "passed" else "needs_repair"
    markdown = _render_failure_analysis(
        report=report,
        validation=validation,
        source=str(artifacts["source"]),
        traceback_block=traceback_block,
        signal_lines=signal_lines,
        implicated_files=implicated_files,
        changed_files=changed_files,
        status=status,
    )
    analysis_path = artifacts["failure_analysis"]
    write_text(analysis_path, markdown)
    _update_manifest_after_failure_analysis(
        run_dir,
        manifest,
        analysis_path=analysis_path,
        status=status,
        source=str(artifacts["source"]),
        implicated_files=implicated_files,
    )
    write_code_task_summary(run_dir)
    return FailureAnalysisResult(
        run_dir=paths.run_dir,
        analysis_path=analysis_path,
        status=status,
        implicated_files=tuple(implicated_files),
        source=str(artifacts["source"]),
    )


def _render_failure_analysis(
    *,
    report: dict[str, Any],
    validation: dict[str, Any],
    source: str,
    traceback_block: str,
    signal_lines: list[str],
    implicated_files: list[str],
    changed_files: list[str],
    status: str,
) -> str:
    status_line = str(report.get("status", "unknown"))
    sections = [
        "# Failure Analysis",
        "",
        f"Status: `{status}`",
        f"Source: `{source}`",
        "",
        "## Execution",
        "",
        _execution_summary(report, status_line),
        "",
        "## Validation",
        "",
        _validation_summary(validation),
        "",
        "## Likely Cause",
        "",
        _likely_cause(report, traceback_block, signal_lines),
        "",
        "## Implicated Files",
        "",
        _bullet_list([f"`{path}`" for path in implicated_files]) or "- None found in traceback.",
        "",
        "## Recently Changed Files",
        "",
        _bullet_list([f"`{path}`" for path in changed_files]) or "- No patch metadata found.",
        "",
        "## Error Signals",
        "",
        _bullet_list([f"`{line}`" for line in signal_lines[:12]]) or "- No concise error lines found.",
        "",
    ]
    if traceback_block:
        sections.extend(
            [
                "## Traceback",
                "",
                "```text",
                _clip(traceback_block, max_chars=6000).rstrip(),
                "```",
                "",
            ]
        )
    sections.extend(
        [
            "## Repair Guidance",
            "",
            "- Keep the repair limited to implicated or recently changed files unless the traceback points elsewhere.",
            "- Re-run validation before re-running the benchmark.",
            "- Prefer fixing the smallest failing behavior before broad refactors.",
            "",
        ]
    )
    return "\n".join(sections)


def _execution_summary(report: dict[str, Any], status_line: str) -> str:
    if not report:
        return "- Benchmark was not launched; diagnosis is based on validation evidence."
    return "\n".join(
        [
            f"- Benchmark status: `{status_line}`",
            f"- Return code: `{report.get('returncode')}`",
            f"- Timed out: `{report.get('timed_out')}`",
            f"- Command: `{report.get('command_text', '')}`",
        ]
    )


def _validation_summary(validation: dict[str, Any]) -> str:
    if not validation:
        return "- No validation report found."
    lines = [
        f"- Status: `{validation.get('status', 'unknown')}`",
        f"- Errors: `{validation.get('error_count', 0)}`",
        f"- Warnings: `{validation.get('warning_count', 0)}`",
    ]
    issues = validation.get("issues", [])
    if isinstance(issues, list):
        for issue in issues[:5]:
            if not isinstance(issue, dict):
                continue
            lines.append(
                "- "
                f"{issue.get('severity', 'issue')} "
                f"{issue.get('code', '')} "
                f"in `{issue.get('path', '')}`: "
                f"{issue.get('message', '')}"
            )
    return "\n".join(lines)


def _likely_cause(
    report: dict[str, Any],
    traceback_block: str,
    signal_lines: list[str],
) -> str:
    if not report:
        return "Static validation failed before a benchmark execution report was available."
    if report.get("status") == "passed":
        return "No failure detected in the latest execution report."
    if report.get("status") == "blocked_by_validation":
        return "The benchmark was not launched because static validation reported errors."
    if report.get("timed_out") is True:
        return "The benchmark exceeded the configured timeout."
    if traceback_block:
        last = _last_nonempty_line(traceback_block)
        return f"The latest traceback ends with: `{last}`"
    if signal_lines:
        return f"The strongest error signal is: `{signal_lines[0]}`"
    return "The benchmark returned a non-zero status without a concise traceback."


def _traceback_block(stderr: str) -> str:
    match = TRACEBACK_RE.search(stderr)
    return match.group(0).strip() if match else ""


def _implicated_files(text: str, workspace_dir: Path) -> list[str]:
    workspace = workspace_dir.resolve()
    files: list[str] = []
    for match in FILE_LINE_RE.finditer(text):
        path = Path(match.group(1))
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if not is_relative_to(resolved, workspace):
            continue
        rel = resolved.relative_to(workspace).as_posix()
        if rel not in files:
            files.append(rel)
    return files


def _signal_lines(stdout: str, stderr: str) -> list[str]:
    found: list[str] = []
    for line in (stderr + "\n" + stdout).splitlines():
        stripped = " ".join(line.strip().split())
        if not stripped:
            continue
        if any(signal in stripped for signal in SIGNAL_LINES):
            found.append(_clip(stripped, max_chars=240))
    return found


def _changed_files(manifest: dict[str, Any]) -> list[str]:
    patch = manifest.get("patch", {})
    if isinstance(patch, dict):
        value = patch.get("changed_files")
        if isinstance(value, list):
            return [str(item) for item in value if isinstance(item, str)]
    return []


def _validation_failed(validation: dict[str, Any]) -> bool:
    return validation.get("status") == "failed" or _int_value(validation.get("error_count")) > 0


def _int_value(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _validation_issue_files(validation: dict[str, Any], workspace_dir: Path) -> list[str]:
    issues = validation.get("issues", [])
    if not isinstance(issues, list):
        return []
    workspace = workspace_dir.resolve()
    files: list[str] = []
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        value = issue.get("path")
        if not isinstance(value, str) or not value:
            continue
        rel = Path(value)
        if rel.is_absolute() or ".." in rel.parts:
            continue
        resolved = (workspace / rel).resolve()
        if not is_relative_to(resolved, workspace):
            continue
        normalized = rel.as_posix()
        if normalized not in files:
            files.append(normalized)
    return files


def _validation_signal_lines(validation: dict[str, Any]) -> list[str]:
    issues = validation.get("issues", [])
    if not isinstance(issues, list):
        return []
    lines: list[str] = []
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        severity = issue.get("severity", "issue")
        code = issue.get("code", "")
        path = issue.get("path", "")
        message = issue.get("message", "")
        lines.append(_clip(f"{severity} {code} in {path}: {message}", max_chars=240))
    return lines


def _dedupe(items: list[str]) -> list[str]:
    deduped: list[str] = []
    for item in items:
        if item not in deduped:
            deduped.append(item)
    return deduped


def _read_optional(path: Path) -> str:
    return read_text(path) if path.exists() else ""


def _read_optional_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    value = read_json(path)
    return value if isinstance(value, dict) else {}


def _bullet_list(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def _last_nonempty_line(text: str) -> str:
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        if stripped:
            return _clip(stripped, max_chars=240)
    return ""


def _clip(text: str, *, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 18].rstrip() + "... [truncated]"


def _update_manifest_after_failure_analysis(
    run_dir: Path,
    manifest: dict[str, Any],
    *,
    analysis_path: Path,
    status: str,
    source: str,
    implicated_files: list[str],
) -> None:
    layout = manifest_section(manifest, "layout")
    benchmark = manifest.get("benchmark", {})
    latest_label = "patched"
    if isinstance(benchmark, dict):
        latest_label = str(benchmark.get("latest_label") or latest_label)
    analysis_rel = analysis_path.relative_to(run_dir).as_posix()
    layout["failure_analysis"] = analysis_rel
    failure = manifest_section(manifest, "failure_analysis")
    failure.update(
        {
            "status": status,
            "source": source,
            "generated_at": utcnow_iso(),
            "analysis": analysis_rel,
            "run_label": latest_label if source != "validation" else "",
            "implicated_files": implicated_files,
        }
    )
    manifest["layout"] = layout
    manifest["failure_analysis"] = failure
    if status == "needs_repair":
        manifest["status"] = "failure_analyzed"
    save_code_task_manifest(run_dir, manifest)


def _latest_failure_artifacts(paths: Any, manifest: dict[str, Any]) -> dict[str, Any]:
    validation_report = paths.meta_dir / "validation_report.json"
    benchmark = manifest.get("benchmark", {})
    if isinstance(benchmark, dict):
        report_rel = benchmark.get("execution_report")
        stdout_rel = benchmark.get("stdout")
        stderr_rel = benchmark.get("stderr")
        if report_rel and stdout_rel and stderr_rel:
            report = paths.run_dir / str(report_rel)
            run_dir = report.parent
            source = "benchmark"
            report_value = _read_optional_json(report)
            if report_value.get("status") == "blocked_by_validation":
                source = "validation"
            return {
                "source": source,
                "execution_report": report,
                "stdout": paths.run_dir / str(stdout_rel),
                "stderr": paths.run_dir / str(stderr_rel),
                "validation_report": validation_report,
                "failure_analysis": run_dir / "failure_analysis.md",
            }
    return {
        "source": "validation",
        "execution_report": None,
        "stdout": paths.meta_dir / "validation_stdout.txt",
        "stderr": paths.meta_dir / "validation_stderr.txt",
        "validation_report": validation_report,
        "failure_analysis": paths.meta_dir / "failure_analysis.md",
    }
