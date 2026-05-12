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
    """

    run_dir: Path
    analysis_path: Path
    status: str
    implicated_files: tuple[str, ...]


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
    report_path = paths.run_artifact_dir / "execution_report.json"
    if not report_path.exists():
        raise FileNotFoundError(
            f"Missing execution report: {report_path}. Run `simple-ar code-task run` first."
        )
    report = read_json(report_path)
    if not isinstance(report, dict):
        raise RuntimeError(f"Expected JSON object in {report_path}")

    stdout = _read_optional(paths.run_artifact_dir / "stdout.txt")
    stderr = _read_optional(paths.run_artifact_dir / "stderr.txt")
    traceback_block = _traceback_block(stderr)
    implicated_files = _implicated_files(traceback_block or stderr, paths.workspace_dir)
    signal_lines = _signal_lines(stdout, stderr)
    validation = _read_optional_json(paths.meta_dir / "validation_report.json")
    changed_files = _changed_files(manifest)

    status = "no_failure" if report.get("status") == "passed" else "needs_repair"
    markdown = _render_failure_analysis(
        report=report,
        validation=validation,
        traceback_block=traceback_block,
        signal_lines=signal_lines,
        implicated_files=implicated_files,
        changed_files=changed_files,
        status=status,
    )
    analysis_path = paths.run_artifact_dir / "failure_analysis.md"
    write_text(analysis_path, markdown)
    _update_manifest_after_failure_analysis(
        run_dir,
        manifest,
        status=status,
        implicated_files=implicated_files,
    )
    write_code_task_summary(run_dir)
    return FailureAnalysisResult(
        run_dir=paths.run_dir,
        analysis_path=analysis_path,
        status=status,
        implicated_files=tuple(implicated_files),
    )


def _render_failure_analysis(
    *,
    report: dict[str, Any],
    validation: dict[str, Any],
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
        "",
        "## Execution",
        "",
        f"- Benchmark status: `{status_line}`",
        f"- Return code: `{report.get('returncode')}`",
        f"- Timed out: `{report.get('timed_out')}`",
        f"- Command: `{report.get('command_text', '')}`",
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
    status: str,
    implicated_files: list[str],
) -> None:
    layout = manifest_section(manifest, "layout")
    layout["failure_analysis"] = "code_task/run/failure_analysis.md"
    failure = manifest_section(manifest, "failure_analysis")
    failure.update(
        {
            "status": status,
            "generated_at": utcnow_iso(),
            "analysis": "code_task/run/failure_analysis.md",
            "implicated_files": implicated_files,
        }
    )
    manifest["layout"] = layout
    manifest["failure_analysis"] = failure
    if status == "needs_repair":
        manifest["status"] = "failure_analyzed"
    save_code_task_manifest(run_dir, manifest)
