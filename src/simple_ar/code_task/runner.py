from __future__ import annotations

import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from simple_ar.artifacts import write_json, write_text
from simple_ar.code_task.state import (
    code_task_paths,
    load_code_task_manifest,
    manifest_section,
    save_code_task_manifest,
    utcnow_iso,
)
from simple_ar.code_task.validation import validate_code_task
from simple_ar.experiment.metrics import parse_metric_lines


CONTROL_TOKENS = {"|", "||", "&&", ";", "<", ">", ">>", "2>", "2>>"}
OUTPUT_CHAR_LIMIT = 200_000


class CodeTaskRunError(RuntimeError):
    """Raised when a code-task benchmark cannot be launched safely."""


@dataclass(frozen=True)
class CodeTaskRunResult:
    """Captured result from running a code-task benchmark.

    Args:
        run_dir: Code-task run directory.
        report_path: Structured execution report.
        stdout_path: Captured stdout path.
        stderr_path: Captured stderr path.
        metrics_path: Parsed metrics path.
        status: ``passed``, ``failed``, ``timed_out``, or
            ``blocked_by_validation``.
        returncode: Subprocess return code, or ``None`` for timeout/blocking.
        timed_out: Whether execution timed out.
        metrics: Parsed ``name: value`` metric lines from stdout.
    """

    run_dir: Path
    report_path: Path
    stdout_path: Path
    stderr_path: Path
    metrics_path: Path
    status: str
    returncode: int | None
    timed_out: bool
    metrics: dict[str, float] = field(default_factory=dict)


def run_code_task_benchmark(
    run_dir: Path,
    *,
    command: str | None = None,
    timeout_sec: int = 60,
    skip_validation: bool = False,
) -> CodeTaskRunResult:
    """Run the recorded benchmark command inside the copied workspace.

    Args:
        run_dir: Code-task run directory.
        command: Optional command override. When omitted, the command recorded
            during ``code-task init`` is used.
        timeout_sec: Maximum runtime in seconds.
        skip_validation: Run even when static validation has not passed.

    Returns:
        Execution artifacts and status.

    Raises:
        CodeTaskRunError: If the command is missing, unsafe, or timeout is
            invalid.
    """
    if timeout_sec < 1:
        raise CodeTaskRunError("timeout_sec must be at least 1")

    manifest = load_code_task_manifest(run_dir)
    paths = code_task_paths(run_dir)
    if not paths.workspace_dir.is_dir():
        raise FileNotFoundError(f"Missing code-task workspace: {paths.workspace_dir}")
    paths.run_artifact_dir.mkdir(parents=True, exist_ok=True)

    command_text = command or _benchmark_command(manifest)
    if not command_text:
        raise CodeTaskRunError(
            "No benchmark command recorded. Pass --command or rerun code-task init "
            "with --benchmark-command."
        )
    command_args = _split_command(command_text)

    if not skip_validation:
        validation = validate_code_task(run_dir)
        if validation.error_count:
            return _write_blocked_result(
                run_dir,
                command_text=command_text,
                command_args=command_args,
                timeout_sec=timeout_sec,
                reason="Static validation reported errors.",
            )
        manifest = load_code_task_manifest(run_dir)

    started = time.monotonic()
    try:
        completed = subprocess.run(
            command_args,
            cwd=paths.workspace_dir,
            env=_safe_env(paths.workspace_dir),
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
        duration_sec = round(time.monotonic() - started, 3)
        stdout, stdout_truncated = _clip_output(completed.stdout)
        stderr, stderr_truncated = _clip_output(completed.stderr)
        metrics = parse_metric_lines(stdout)
        status = "passed" if completed.returncode == 0 else "failed"
        return _write_execution_result(
            run_dir,
            manifest=manifest,
            command_text=command_text,
            command_args=command_args,
            timeout_sec=timeout_sec,
            duration_sec=duration_sec,
            status=status,
            returncode=completed.returncode,
            timed_out=False,
            stdout=stdout,
            stderr=stderr,
            metrics=metrics,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
        )
    except subprocess.TimeoutExpired as exc:
        duration_sec = round(time.monotonic() - started, 3)
        stdout, stdout_truncated = _clip_output(_output_text(exc.stdout))
        stderr_text = _output_text(exc.stderr)
        if stderr_text:
            stderr_text += "\n"
        stderr_text += f"Timed out after {timeout_sec} seconds."
        stderr, stderr_truncated = _clip_output(stderr_text)
        metrics = parse_metric_lines(stdout)
        return _write_execution_result(
            run_dir,
            manifest=manifest,
            command_text=command_text,
            command_args=command_args,
            timeout_sec=timeout_sec,
            duration_sec=duration_sec,
            status="timed_out",
            returncode=None,
            timed_out=True,
            stdout=stdout,
            stderr=stderr,
            metrics=metrics,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
        )


def _write_blocked_result(
    run_dir: Path,
    *,
    command_text: str,
    command_args: list[str],
    timeout_sec: int,
    reason: str,
) -> CodeTaskRunResult:
    manifest = load_code_task_manifest(run_dir)
    return _write_execution_result(
        run_dir,
        manifest=manifest,
        command_text=command_text,
        command_args=command_args,
        timeout_sec=timeout_sec,
        duration_sec=0.0,
        status="blocked_by_validation",
        returncode=None,
        timed_out=False,
        stdout="",
        stderr=reason + "\n",
        metrics={},
        stdout_truncated=False,
        stderr_truncated=False,
    )


def _write_execution_result(
    run_dir: Path,
    *,
    manifest: dict[str, Any],
    command_text: str,
    command_args: list[str],
    timeout_sec: int,
    duration_sec: float,
    status: str,
    returncode: int | None,
    timed_out: bool,
    stdout: str,
    stderr: str,
    metrics: dict[str, float],
    stdout_truncated: bool,
    stderr_truncated: bool,
) -> CodeTaskRunResult:
    paths = code_task_paths(run_dir)
    paths.run_artifact_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = paths.run_artifact_dir / "stdout.txt"
    stderr_path = paths.run_artifact_dir / "stderr.txt"
    metrics_path = paths.run_artifact_dir / "metrics.json"
    report_path = paths.run_artifact_dir / "execution_report.json"

    write_text(stdout_path, stdout or "")
    write_text(stderr_path, stderr or "")
    write_json(metrics_path, metrics)
    report = {
        "schema_version": 1,
        "generated_at": utcnow_iso(),
        "status": status,
        "command_text": command_text,
        "command": command_args,
        "cwd": str(paths.workspace_dir),
        "timeout_sec": timeout_sec,
        "duration_sec": duration_sec,
        "returncode": returncode,
        "timed_out": timed_out,
        "stdout": "code_task/run/stdout.txt",
        "stderr": "code_task/run/stderr.txt",
        "metrics": "code_task/run/metrics.json",
        "metric_values": metrics,
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
    }
    write_json(report_path, report)
    _update_manifest_after_run(
        run_dir,
        manifest,
        status=status,
        command_text=command_text,
        returncode=returncode,
        timed_out=timed_out,
        metric_values=metrics,
    )
    return CodeTaskRunResult(
        run_dir=paths.run_dir,
        report_path=report_path,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        metrics_path=metrics_path,
        status=status,
        returncode=returncode,
        timed_out=timed_out,
        metrics=metrics,
    )


def _split_command(command_text: str) -> list[str]:
    try:
        args = shlex.split(command_text, posix=os.name != "nt")
    except ValueError as exc:
        raise CodeTaskRunError(f"Could not parse benchmark command: {exc}") from exc
    if not args:
        raise CodeTaskRunError("Benchmark command is empty")
    if any(token in CONTROL_TOKENS for token in args):
        raise CodeTaskRunError(
            "Shell control operators are not supported in benchmark commands. "
            "Use a direct command such as `python -m unittest discover -s tests`."
        )
    if args[0] in {"python", "python3"}:
        args[0] = sys.executable
    return args


def _safe_env(workspace_dir: Path) -> dict[str, str]:
    allowed = {
        "APPDATA",
        "COMSPEC",
        "HOME",
        "LOCALAPPDATA",
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "WINDIR",
    }
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    python_paths = [str(workspace_dir), str(workspace_dir / "src")]
    existing = os.environ.get("PYTHONPATH")
    if existing:
        python_paths.append(existing)
    env["PYTHONPATH"] = os.pathsep.join(python_paths)
    env["SIMPLE_AR_CODE_TASK"] = "1"
    return env


def _clip_output(text: str) -> tuple[str, bool]:
    if len(text) <= OUTPUT_CHAR_LIMIT:
        return text, False
    suffix = "\n... [truncated by SimpleAutoResearch]\n"
    return text[: OUTPUT_CHAR_LIMIT - len(suffix)] + suffix, True


def _output_text(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _benchmark_command(manifest: dict[str, Any]) -> str:
    benchmark = manifest.get("benchmark", {})
    if isinstance(benchmark, dict):
        command = benchmark.get("command")
        return str(command) if command else ""
    return ""


def _update_manifest_after_run(
    run_dir: Path,
    manifest: dict[str, Any],
    *,
    status: str,
    command_text: str,
    returncode: int | None,
    timed_out: bool,
    metric_values: dict[str, float],
) -> None:
    layout = manifest_section(manifest, "layout")
    layout["run"] = "code_task/run"
    layout["execution_report"] = "code_task/run/execution_report.json"
    benchmark = manifest_section(manifest, "benchmark")
    benchmark.update(
        {
            "command": command_text,
            "executed": True,
            "last_status": status,
            "last_run_at": utcnow_iso(),
            "execution_report": "code_task/run/execution_report.json",
            "stdout": "code_task/run/stdout.txt",
            "stderr": "code_task/run/stderr.txt",
            "metrics": "code_task/run/metrics.json",
            "returncode": returncode,
            "timed_out": timed_out,
            "metric_values": metric_values,
        }
    )
    manifest["layout"] = layout
    manifest["benchmark"] = benchmark
    if status == "passed":
        manifest["status"] = "benchmark_passed"
    elif status == "blocked_by_validation":
        manifest["status"] = "benchmark_blocked"
    else:
        manifest["status"] = "benchmark_failed"
    save_code_task_manifest(run_dir, manifest)
